"""
Experimental Gemini + OSM roof assessment workflow.

This module deliberately does not replace the active Stage 1 pipeline. It uses
Stage 1's existing OSM/local footprints and cached Google satellite tiles, asks
Gemini to assess the outlined roof crop, and writes comparison outputs under
``data/output/experiments``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
from google import genai
from google.genai import types
from PIL import Image

from config.settings import DEFAULT_TILE_SIZE, DEFAULT_ZOOM, GEMINI_API_KEY, OUTPUT_DIR, TILES_DIR
from shared.file_io import ensure_dir
from shared.geo_utils import latlon_to_tile, tile_centre_latlon
from shared.logging_config import setup_logging
from stage1_segmentation.building_footprint_segmenter import _latlon_to_pixel

logger = setup_logging("gemini_osm_experiment")

GEMINI_OSM_MODEL = "gemini-2.5-flash"
EXPERIMENT_OUTPUT_DIR = OUTPUT_DIR / "experiments"
DEFAULT_RATE_LIMIT_DELAY_SECONDS = 4.0
MEDIA_RESOLUTIONS = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}

ROOF_COLOURS = {"white", "light_grey", "dark_grey", "red", "brown", "blue", "green", "other", "unknown"}
ROOF_MATERIALS = {"metal", "tile", "concrete", "terracotta", "membrane", "solar_panel", "other", "unknown"}
ROOF_SHAPES = {"flat", "gable", "hip", "skillion", "complex", "unknown"}
PITCH_CLASSES = {"flat", "low", "medium", "steep", "unknown"}
BOUNDARY_QUALITIES = {"matches_osm", "osm_overhang", "osm_underhang", "osm_shifted", "unclear"}


@dataclass
class CropContext:
    """A building crop plus geometry needed to map crop pixels back to a tile."""

    image: Image.Image
    tile_path: Path
    crop_box: tuple[int, int, int, int]
    osm_polygon_crop_px: list[list[int]]


@dataclass
class GeminiRoofAssessment:
    """Normalised Gemini response for one OSM building footprint."""

    building_id: str
    roof_visible: bool
    boundary_quality: str
    roof_colour: str
    roof_material: str
    roof_shape: str
    pitch_class: str
    pitch_deg_estimate: float | None
    confidence: float
    suggested_boundary_polygon_px: list[list[int]]
    warnings: list[str]
    model: str
    tile: str
    crop_box: tuple[int, int, int, int]
    osm_polygon_crop_px: list[list[int]]


GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "roof_visible": {
            "type": "boolean",
            "description": "Whether a rooftop is visible inside the red OSM outline.",
        },
        "boundary_quality": {
            "type": "string",
            "enum": sorted(BOUNDARY_QUALITIES),
            "description": "How well the red OSM footprint matches the visible roof outline.",
        },
        "suggested_boundary_polygon_px": {
            "type": "array",
            "description": "Optional simple roof polygon in crop pixel coordinates.",
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 2,
            },
        },
        "roof_colour": {"type": "string", "enum": sorted(ROOF_COLOURS)},
        "roof_material": {"type": "string", "enum": sorted(ROOF_MATERIALS)},
        "roof_shape": {"type": "string", "enum": sorted(ROOF_SHAPES)},
        "pitch_class": {"type": "string", "enum": sorted(PITCH_CLASSES)},
        "pitch_deg_estimate": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 60,
            "description": "Coarse visual estimate only; null if not defensible from the image.",
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "roof_visible",
        "boundary_quality",
        "suggested_boundary_polygon_px",
        "roof_colour",
        "roof_material",
        "roof_shape",
        "pitch_class",
        "pitch_deg_estimate",
        "confidence",
        "warnings",
    ],
}


PROMPT = """
You are assessing one Melbourne rooftop from a Google satellite crop.

The red outline is the OSM/local building footprint. Analyse only the roof
inside that red outline. Ignore neighbouring buildings, roads, trees, cars, and
shadows outside the outline.

Return JSON matching the schema. Treat pitch as a coarse visual inference only:
- flat: 0-5 degrees
- low: 5-15 degrees
- medium: 15-30 degrees
- steep: 30+ degrees
- unknown: insufficient visible evidence

Do not invent precision. If roof pitch, material, or boundary is uncertain, set
the relevant value to "unknown", lower confidence, and add a warning. Use
"suggested_boundary_polygon_px" only when a simple 4-8 vertex roof outline is
clearly visible in crop pixel coordinates.
"""


def _suburb_key(suburb_name: str) -> str:
    return suburb_name.lower().replace(" ", "_")


def _experiment_paths(suburb_name: str) -> tuple[Path, Path]:
    suburb_key = _suburb_key(suburb_name)
    out_dir = ensure_dir(EXPERIMENT_OUTPUT_DIR)
    return (
        out_dir / f"gemini_osm_stage1_{suburb_key}.jsonl",
        out_dir / f"gemini_osm_stage1_{suburb_key}.csv",
    )


def _load_stage1_inputs(suburb_name: str) -> tuple[pd.DataFrame, list[list[list[float]]]]:
    suburb_key = _suburb_key(suburb_name)
    parquet_path = OUTPUT_DIR / f"stage1_{suburb_key}.parquet"
    csv_path = OUTPUT_DIR / f"stage1_{suburb_key}.csv"
    polygons_path = OUTPUT_DIR / f"stage1_{suburb_key}_polygons.json"

    if parquet_path.exists():
        df = pd.read_parquet(parquet_path)
    elif csv_path.exists():
        df = pd.read_csv(csv_path)
    else:
        raise FileNotFoundError(
            f"No Stage 1 table found for {suburb_name}. Run Stage 1 first, or provide cached outputs."
        )

    if not polygons_path.exists():
        raise FileNotFoundError(
            f"Missing polygon sidecar: {polygons_path}. Gemini+OSM experiment needs OSM polygons."
        )

    polygons = json.loads(polygons_path.read_text())
    if len(polygons) != len(df):
        raise ValueError(
            f"Polygon sidecar row count ({len(polygons)}) does not match Stage 1 table ({len(df)})."
        )
    return df, polygons


def _tile_path_for_row(row: pd.Series, suburb_name: str, zoom: int) -> Path:
    tile_x, tile_y = latlon_to_tile(float(row["lat"]), float(row["lon"]), zoom)
    suburb_key = _suburb_key(suburb_name)
    return TILES_DIR / suburb_key / f"{suburb_key}_{zoom}_{tile_x}_{tile_y}.png"


def _polygon_to_tile_pixels(
    polygon_latlon: list[list[float]],
    tile_x: int,
    tile_y: int,
    zoom: int,
) -> list[list[int]]:
    tile_lat, tile_lon = tile_centre_latlon(tile_x, tile_y, zoom)
    return [
        list(_latlon_to_pixel(lat, lon, tile_lat, tile_lon, zoom, DEFAULT_TILE_SIZE))
        for lon, lat in polygon_latlon
    ]


def build_building_crop(
    row: pd.Series,
    polygon_latlon: list[list[float]],
    suburb_name: str,
    zoom: int = DEFAULT_ZOOM,
    padding_px: int = 48,
) -> CropContext | None:
    """
    Build a satellite crop with the OSM footprint outlined in red.

    Returns None if the matching cached tile is unavailable or the projected
    polygon is invalid/outside the tile.
    """
    tile_path = _tile_path_for_row(row, suburb_name, zoom)
    if not tile_path.exists():
        logger.debug("Skipping %s: missing tile %s", row["building_id"], tile_path.name)
        return None

    tile_x, tile_y = latlon_to_tile(float(row["lat"]), float(row["lon"]), zoom)
    polygon_px = _polygon_to_tile_pixels(polygon_latlon, tile_x, tile_y, zoom)
    if len(polygon_px) < 3:
        return None

    pts = np.array(polygon_px, dtype=np.int32)
    x_min, y_min = np.maximum(pts.min(axis=0) - padding_px, 0)
    x_max, y_max = np.minimum(pts.max(axis=0) + padding_px, DEFAULT_TILE_SIZE - 1)
    if x_max <= x_min or y_max <= y_min:
        return None

    image_bgr = cv2.imread(str(tile_path), cv2.IMREAD_COLOR)
    if image_bgr is None:
        logger.warning("Skipping %s: could not read %s", row["building_id"], tile_path)
        return None

    image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    crop = image_rgb[y_min : y_max + 1, x_min : x_max + 1].copy()
    polygon_crop_px = [[int(x - x_min), int(y - y_min)] for x, y in polygon_px]

    overlay_pts = np.array(polygon_crop_px, dtype=np.int32)
    cv2.polylines(crop, [overlay_pts], isClosed=True, color=(255, 0, 0), thickness=3)
    cv2.circle(crop, tuple(overlay_pts.mean(axis=0).astype(int)), 4, (255, 0, 0), -1)

    return CropContext(
        image=Image.fromarray(crop),
        tile_path=tile_path,
        crop_box=(int(x_min), int(y_min), int(x_max), int(y_max)),
        osm_polygon_crop_px=polygon_crop_px,
    )


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    """Parse a Gemini JSON object, tolerating markdown fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start : end + 1])

    if not isinstance(value, dict):
        raise ValueError("Gemini response was not a JSON object")
    return value


def _enum_value(value: Any, allowed: set[str], default: str = "unknown") -> str:
    normalised = str(value).strip().lower().replace(" ", "_") if value is not None else default
    return normalised if normalised in allowed else default


def _confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _pitch_estimate(value: Any) -> float | None:
    if value is None:
        return None
    try:
        pitch = float(value)
    except (TypeError, ValueError):
        return None
    if pitch < 0 or pitch > 60:
        return None
    return round(pitch, 2)


def _polygon_value(value: Any, width: int, height: int) -> list[list[int]]:
    if not isinstance(value, list):
        return []

    vertices: list[list[int]] = []
    for point in value:
        if not isinstance(point, list | tuple) or len(point) != 2:
            continue
        try:
            x = max(0, min(width - 1, int(round(float(point[0])))))
            y = max(0, min(height - 1, int(round(float(point[1])))))
        except (TypeError, ValueError):
            continue
        vertices.append([x, y])

    return vertices if len(vertices) >= 3 else []


def normalise_assessment(
    building_id: str,
    raw: dict[str, Any],
    crop: CropContext,
    model: str,
) -> GeminiRoofAssessment:
    """Validate and normalise one Gemini response."""
    width, height = crop.image.size
    warnings = raw.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [str(warnings)]

    return GeminiRoofAssessment(
        building_id=building_id,
        roof_visible=bool(raw.get("roof_visible", False)),
        boundary_quality=_enum_value(raw.get("boundary_quality"), BOUNDARY_QUALITIES, "unclear"),
        roof_colour=_enum_value(raw.get("roof_colour"), ROOF_COLOURS),
        roof_material=_enum_value(raw.get("roof_material"), ROOF_MATERIALS),
        roof_shape=_enum_value(raw.get("roof_shape"), ROOF_SHAPES),
        pitch_class=_enum_value(raw.get("pitch_class"), PITCH_CLASSES),
        pitch_deg_estimate=_pitch_estimate(raw.get("pitch_deg_estimate")),
        confidence=_confidence(raw.get("confidence")),
        suggested_boundary_polygon_px=_polygon_value(
            raw.get("suggested_boundary_polygon_px"), width, height
        ),
        warnings=[str(item) for item in warnings],
        model=model,
        tile=crop.tile_path.name,
        crop_box=crop.crop_box,
        osm_polygon_crop_px=crop.osm_polygon_crop_px,
    )


def assess_crop_with_gemini(
    client: genai.Client,
    building_id: str,
    crop: CropContext,
    model: str = GEMINI_OSM_MODEL,
    media_resolution: str = "high",
) -> GeminiRoofAssessment:
    """Send one outlined building crop to Gemini and return a normalised assessment."""
    if media_resolution not in MEDIA_RESOLUTIONS:
        raise ValueError(
            f"media_resolution must be one of {sorted(MEDIA_RESOLUTIONS)}; got {media_resolution!r}"
        )

    response = client.models.generate_content(
        model=model,
        contents=[crop.image, PROMPT],
        config=types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=1200,
            response_mime_type="application/json",
            response_json_schema=GEMINI_RESPONSE_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            media_resolution=MEDIA_RESOLUTIONS[media_resolution],
        ),
    )
    raw = _extract_json_object(response.text)
    return normalise_assessment(building_id, raw, crop, model)


def _load_completed_building_ids(jsonl_path: Path) -> set[str]:
    if not jsonl_path.exists():
        return set()

    completed: set[str] = set()
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            completed.add(str(json.loads(line)["building_id"]))
        except (KeyError, json.JSONDecodeError):
            continue
    return completed


def _write_csv_from_jsonl(jsonl_path: Path, csv_path: Path) -> None:
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines() if line.strip()]
    if rows:
        pd.DataFrame(rows).to_csv(csv_path, index=False)


def run_gemini_osm_experiment(
    suburb_name: str,
    zoom: int = DEFAULT_ZOOM,
    max_buildings: int = 10,
    start_index: int = 0,
    rate_limit_delay: float = DEFAULT_RATE_LIMIT_DELAY_SECONDS,
    model: str = GEMINI_OSM_MODEL,
    media_resolution: str = "high",
    dry_run: bool = False,
    overwrite: bool = False,
) -> pd.DataFrame:
    """
    Run the opt-in Gemini+OSM experiment for a bounded building sample.

    The active Stage 1 outputs are read-only inputs. Results are written to
    ``data/output/experiments`` and can be safely discarded.
    """
    if max_buildings <= 0:
        raise ValueError("max_buildings must be positive")
    if not dry_run and not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not set in .env")

    df, polygons = _load_stage1_inputs(suburb_name)
    jsonl_path, csv_path = _experiment_paths(suburb_name)
    if overwrite:
        jsonl_path.unlink(missing_ok=True)
        csv_path.unlink(missing_ok=True)

    completed = _load_completed_building_ids(jsonl_path)
    client = None if dry_run else genai.Client(api_key=GEMINI_API_KEY)

    rows: list[dict[str, Any]] = []
    processed = 0
    attempted = 0
    skipped_missing_inputs = 0
    candidates = df.iloc[start_index:]

    logger.info(
        "Gemini+OSM experiment for %s: collecting up to %d buildings from row %d",
        suburb_name,
        max_buildings,
        start_index,
    )

    for row_index, row in candidates.iterrows():
        if processed >= max_buildings:
            break

        building_id = str(row["building_id"])
        if building_id in completed:
            logger.info("Skipping %s: already in %s", building_id, jsonl_path.name)
            continue

        crop = build_building_crop(row, polygons[row_index], suburb_name, zoom)
        if crop is None:
            skipped_missing_inputs += 1
            continue

        attempted += 1
        if dry_run:
            assessment = GeminiRoofAssessment(
                building_id=building_id,
                roof_visible=True,
                boundary_quality="unclear",
                roof_colour="unknown",
                roof_material="unknown",
                roof_shape="unknown",
                pitch_class="unknown",
                pitch_deg_estimate=None,
                confidence=0.0,
                suggested_boundary_polygon_px=[],
                warnings=["dry_run: Gemini API not called"],
                model=model,
                tile=crop.tile_path.name,
                crop_box=crop.crop_box,
                osm_polygon_crop_px=crop.osm_polygon_crop_px,
            )
        else:
            assert client is not None
            assessment = assess_crop_with_gemini(
                client,
                building_id,
                crop,
                model,
                media_resolution=media_resolution,
            )

        result = {
            **row.to_dict(),
            **asdict(assessment),
            "stage1_roof_material": row.get("roof_material"),
            "stage1_roof_colour": row.get("roof_colour"),
            "stage1_pitch_deg": row.get("pitch_deg"),
            "row_index": int(row_index),
        }
        rows.append(result)

        with jsonl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result, separators=(",", ":")) + "\n")
        processed += 1

        if not dry_run and processed < max_buildings:
            time.sleep(rate_limit_delay)

    if jsonl_path.exists():
        _write_csv_from_jsonl(jsonl_path, csv_path)
    logger.info(
        "Gemini+OSM experiment summary: %d processed, %d attempted, %d skipped before crop/API",
        processed,
        attempted,
        skipped_missing_inputs,
    )
    logger.info("Gemini+OSM experiment wrote %s and %s", jsonl_path, csv_path)
    return pd.DataFrame(rows)
