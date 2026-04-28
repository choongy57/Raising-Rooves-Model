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
EXPERIMENT_VERSION = "gemini_osm_v2_conservative_qa"
EXPERIMENT_OUTPUT_DIR = OUTPUT_DIR / "experiments"
DEFAULT_RATE_LIMIT_DELAY_SECONDS = 4.0
MEDIA_RESOLUTIONS = {
    "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
    "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
    "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
}

ROOF_COLOURS = {"white", "light_grey", "dark_grey", "red", "brown", "blue", "green", "other", "unknown"}
ROOF_MATERIALS = {"metal", "tile", "concrete", "terracotta", "membrane", "solar_panel", "mixed", "other", "unknown"}
ROOF_SHAPES = {"flat", "gable", "hip", "skillion", "sawtooth", "complex", "mixed", "unknown"}
PITCH_CLASSES = {"flat", "low", "medium", "steep", "unknown"}
BOUNDARY_QUALITIES = {
    "matches_osm",
    "osm_overhang",
    "osm_underhang",
    "osm_shifted",
    "multiple_roofs",
    "partial_roof",
    "unclear",
}
IMAGE_QUALITIES = {"clear", "blurry", "shadowed", "occluded", "mixed", "unknown"}
MATERIAL_EVIDENCE = {
    "ribbed_lines",
    "tile_pattern",
    "smooth_uniform",
    "solar_panels",
    "visual_only",
    "insufficient",
}
PITCH_BASES = {
    "visible_facets",
    "ridge_geometry",
    "shadow_support",
    "flat_roof_visual",
    "insufficient",
    "not_observable",
}
QA_ACTIONS = {"accept", "accept_with_warning", "exclude", "needs_manual_review", "needs_dsm"}
QUALITY_FLAGS = {
    "tree_cover",
    "shadow",
    "blurry",
    "low_resolution",
    "solar_panels",
    "partial_roof",
    "ambiguous_material",
    "ambiguous_pitch",
    "ambiguous_boundary",
}


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
    usable_for_stage1: bool
    image_quality: str
    occlusion_fraction: float | None
    boundary_quality: str
    visible_roof_fraction: float | None
    roof_colour: str
    roof_colour_confidence: float
    roof_material: str
    roof_material_confidence: float
    material_evidence: str
    roof_shape: str
    roof_shape_confidence: float
    pitch_observable: bool
    pitch_class: str
    pitch_confidence: float
    pitch_deg_estimate: float | None
    pitch_basis: str
    boundary_confidence: float
    confidence: float
    qa_score: float
    qa_action: str
    suggested_boundary_polygon_px: list[list[int]]
    quality_flags: list[str]
    evidence: str
    warnings: list[str]
    model: str
    tile: str
    crop_box: tuple[int, int, int, int]
    osm_polygon_crop_px: list[list[int]]
    experiment_version: str


GEMINI_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "roof_visible": {
            "type": "boolean",
            "description": "Whether a rooftop is visible inside the red OSM outline.",
        },
        "usable_for_stage1": {
            "type": "boolean",
            "description": "Whether the image evidence is good enough to use Gemini attributes automatically.",
        },
        "image_quality": {"type": "string", "enum": sorted(IMAGE_QUALITIES)},
        "occlusion_fraction": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
            "description": "Estimated footprint fraction hidden by tree cover, shadow, blur, or other obstruction.",
        },
        "visible_roof_fraction": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
            "description": "Estimated fraction of the red polygon that contains visible roof.",
        },
        "boundary_quality": {
            "type": "string",
            "enum": sorted(BOUNDARY_QUALITIES),
            "description": "How well the red OSM footprint matches the visible roof outline.",
        },
        "boundary_confidence": {"type": "number", "minimum": 0, "maximum": 1},
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
        "roof_colour_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "roof_material": {"type": "string", "enum": sorted(ROOF_MATERIALS)},
        "roof_material_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "material_evidence": {"type": "string", "enum": sorted(MATERIAL_EVIDENCE)},
        "roof_shape": {"type": "string", "enum": sorted(ROOF_SHAPES)},
        "roof_shape_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "pitch_observable": {"type": "boolean"},
        "pitch_class": {"type": "string", "enum": sorted(PITCH_CLASSES)},
        "pitch_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "pitch_deg_estimate": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 60,
            "description": "Coarse visual estimate only; null if not defensible from the image.",
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "pitch_basis": {"type": "string", "enum": sorted(PITCH_BASES)},
        "qa_action": {"type": "string", "enum": sorted(QA_ACTIONS)},
        "quality_flags": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(QUALITY_FLAGS)},
        },
        "evidence": {
            "type": "string",
            "description": "One short sentence explaining the visible evidence and uncertainty.",
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "roof_visible",
        "usable_for_stage1",
        "image_quality",
        "occlusion_fraction",
        "visible_roof_fraction",
        "boundary_quality",
        "boundary_confidence",
        "suggested_boundary_polygon_px",
        "roof_colour",
        "roof_colour_confidence",
        "roof_material",
        "roof_material_confidence",
        "material_evidence",
        "roof_shape",
        "roof_shape_confidence",
        "pitch_observable",
        "pitch_class",
        "pitch_confidence",
        "pitch_deg_estimate",
        "pitch_basis",
        "confidence",
        "qa_action",
        "quality_flags",
        "evidence",
        "warnings",
    ],
}


PROMPT = """
You are a conservative roof analyst for Melbourne satellite imagery.

You are given ONE image crop. The red polygon is the OSM/local building
footprint. Analyse ONLY the rooftop inside the red polygon. Ignore neighbouring
buildings, roads, trees, cars, carparks, driveways, bare ground, and shadows
outside the outline.

Primary rule: if the answer is uncertain, return "unknown" or null and lower
the relevant confidence. Do not guess and do not invent precise pitch.

Tasks:
1. Decide whether a visible roof exists inside the red polygon.
2. Estimate visible_roof_fraction: the fraction of the red polygon occupied by
   visible roof pixels, not trees/shadow/ground.
3. Assess whether the red polygon matches the visible roof boundary.
4. Classify roof colour from visible pixels only.
5. Infer broad material only if visual evidence is strong.
6. Infer roof shape and pitch class only if visible geometry supports it.

Boundary labels:
- matches_osm: red polygon closely follows the visible roof/footprint.
- osm_overhang: red polygon extends materially beyond the visible roof.
- osm_underhang: visible roof extends materially beyond the red polygon.
- osm_shifted: red polygon appears offset from the visible roof.
- multiple_roofs: the red polygon contains more than one visible roof/building part.
- partial_roof: only part of the roof is visible inside the crop or outline.
- unclear: trees, shadows, image blur, or occlusion prevent judgement.

Material labels:
- metal: smooth or corrugated sheet appearance, Colorbond-like, large uniform panels.
- tile: repeated roof tile texture or residential tiled appearance.
- concrete: concrete slab or flat commercial roof appearance.
- terracotta: red/orange clay tile appearance.
- membrane: flat synthetic membrane roof appearance.
- solar_panel: visible solar-panel dominated roof section.
- mixed: multiple materials are visibly present inside the red polygon.
- other: visible roof but not covered by the labels.
- unknown: insufficient evidence.

Pitch class:
- flat: 0-5 degrees.
- low: 5-15 degrees.
- medium: 15-30 degrees.
- steep: 30+ degrees.
- unknown: not visually defensible.

Important constraints:
- Nadir satellite imagery cannot reliably measure pitch in degrees.
- Return pitch_deg_estimate only for clearly flat roofs or very obvious pitched roofs.
- If roof_shape is unknown, pitch_class should usually be unknown.
- If pitch is not visually defensible, set pitch_observable false,
  pitch_class unknown, pitch_deg_estimate null, pitch_basis not_observable or
  insufficient, and add ambiguous_pitch.
- For clearly flat commercial roofs, pitch_class may be flat, but
  pitch_deg_estimate should usually remain null unless the roof is visibly flat.
- If tree cover, shadow, blur, solar panels, or partial visibility affects the
  result, add quality_flags and lower confidence.
- Set usable_for_stage1 false unless boundary_quality is matches_osm, roof is
  visible, image quality is usable, and at least colour or material has direct
  visual evidence.
- qa_action should be accept only for high-confidence, low-warning results.
  Use accept_with_warning for usable but imperfect results, needs_manual_review
  for ambiguous boundary/material/visibility, needs_dsm when pitch is the main
  missing value, and exclude when no roof is visible.
- Do not use surrounding houses to infer this roof.
- suggested_boundary_polygon_px is optional. Only return a simple 4-8 vertex
  polygon when the visible roof outline is clearly separable inside the crop.

Return only JSON matching the schema.
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


def _fraction(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


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


def _quality_flags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    flags: list[str] = []
    for item in value:
        flag = str(item).strip().lower().replace(" ", "_")
        if flag in QUALITY_FLAGS and flag not in flags:
            flags.append(flag)
    return flags


def _compute_qa_score(raw: dict[str, Any], quality_flags: list[str]) -> float:
    confidences = [
        _confidence(raw.get("confidence")),
        _confidence(raw.get("boundary_confidence")),
        _confidence(raw.get("roof_colour_confidence")),
        _confidence(raw.get("roof_material_confidence")),
        _confidence(raw.get("roof_shape_confidence")),
    ]
    if bool(raw.get("pitch_observable")):
        confidences.append(_confidence(raw.get("pitch_confidence")))

    score = sum(confidences) / len(confidences)
    if _enum_value(raw.get("boundary_quality"), BOUNDARY_QUALITIES, "unclear") != "matches_osm":
        score -= 0.2
    if not bool(raw.get("roof_visible", False)):
        score -= 0.4
    score -= min(0.3, 0.05 * len(quality_flags))
    return round(max(0.0, min(1.0, score)), 2)


def _qa_action(raw: dict[str, Any], quality_flags: list[str], qa_score: float) -> str:
    model_action = _enum_value(raw.get("qa_action"), QA_ACTIONS, "needs_manual_review")
    boundary_quality = _enum_value(raw.get("boundary_quality"), BOUNDARY_QUALITIES, "unclear")
    pitch_basis = _enum_value(raw.get("pitch_basis"), PITCH_BASES, "not_observable")

    if not bool(raw.get("roof_visible", False)):
        return "exclude"
    if boundary_quality != "matches_osm":
        return "needs_manual_review"
    if {"tree_cover", "blurry", "partial_roof", "ambiguous_boundary"} & set(quality_flags):
        return "needs_manual_review"
    if (
        _enum_value(raw.get("pitch_class"), PITCH_CLASSES) not in {"flat", "unknown"}
        or (
            _enum_value(raw.get("pitch_class"), PITCH_CLASSES) != "unknown"
            and _pitch_estimate(raw.get("pitch_deg_estimate")) is None
            and pitch_basis != "flat_roof_visual"
        )
    ):
        return "needs_dsm"
    if model_action == "accept" and qa_score < 0.8:
        return "accept_with_warning"
    return model_action


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
    quality_flags = _quality_flags(raw.get("quality_flags"))
    qa_score = _compute_qa_score(raw, quality_flags)
    qa_action = _qa_action(raw, quality_flags, qa_score)

    qa_usable = (
        bool(raw.get("usable_for_stage1", False))
        and qa_action in {"accept", "accept_with_warning", "needs_dsm"}
    )

    return GeminiRoofAssessment(
        building_id=building_id,
        roof_visible=bool(raw.get("roof_visible", False)),
        usable_for_stage1=qa_usable,
        image_quality=_enum_value(raw.get("image_quality"), IMAGE_QUALITIES),
        occlusion_fraction=_fraction(raw.get("occlusion_fraction")),
        boundary_quality=_enum_value(raw.get("boundary_quality"), BOUNDARY_QUALITIES, "unclear"),
        visible_roof_fraction=_fraction(raw.get("visible_roof_fraction")),
        roof_colour=_enum_value(raw.get("roof_colour"), ROOF_COLOURS),
        roof_colour_confidence=_confidence(raw.get("roof_colour_confidence")),
        roof_material=_enum_value(raw.get("roof_material"), ROOF_MATERIALS),
        roof_material_confidence=_confidence(raw.get("roof_material_confidence")),
        material_evidence=_enum_value(raw.get("material_evidence"), MATERIAL_EVIDENCE, "insufficient"),
        roof_shape=_enum_value(raw.get("roof_shape"), ROOF_SHAPES),
        roof_shape_confidence=_confidence(raw.get("roof_shape_confidence")),
        pitch_observable=bool(raw.get("pitch_observable", False)),
        pitch_class=_enum_value(raw.get("pitch_class"), PITCH_CLASSES),
        pitch_confidence=_confidence(raw.get("pitch_confidence")),
        pitch_deg_estimate=_pitch_estimate(raw.get("pitch_deg_estimate")),
        pitch_basis=_enum_value(raw.get("pitch_basis"), PITCH_BASES, "not_observable"),
        boundary_confidence=_confidence(raw.get("boundary_confidence")),
        confidence=_confidence(raw.get("confidence")),
        qa_score=qa_score,
        qa_action=qa_action,
        suggested_boundary_polygon_px=_polygon_value(
            raw.get("suggested_boundary_polygon_px"), width, height
        ),
        quality_flags=quality_flags,
        evidence=str(raw.get("evidence", "")),
        warnings=[str(item) for item in warnings],
        model=model,
        tile=crop.tile_path.name,
        crop_box=crop.crop_box,
        osm_polygon_crop_px=crop.osm_polygon_crop_px,
        experiment_version=EXPERIMENT_VERSION,
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


def _load_completed_building_ids(jsonl_path: Path, experiment_version: str) -> set[str]:
    if not jsonl_path.exists():
        return set()

    completed: set[str] = set()
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if row.get("experiment_version") == experiment_version:
                completed.add(str(row["building_id"]))
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

    completed = _load_completed_building_ids(jsonl_path, EXPERIMENT_VERSION)
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
                usable_for_stage1=False,
                image_quality="unknown",
                occlusion_fraction=None,
                boundary_quality="unclear",
                visible_roof_fraction=None,
                roof_colour="unknown",
                roof_colour_confidence=0.0,
                roof_material="unknown",
                roof_material_confidence=0.0,
                material_evidence="insufficient",
                roof_shape="unknown",
                roof_shape_confidence=0.0,
                pitch_observable=False,
                pitch_class="unknown",
                pitch_confidence=0.0,
                pitch_deg_estimate=None,
                pitch_basis="not_observable",
                boundary_confidence=0.0,
                confidence=0.0,
                qa_score=0.0,
                qa_action="needs_manual_review",
                suggested_boundary_polygon_px=[],
                quality_flags=[],
                evidence="dry_run: Gemini API not called",
                warnings=["dry_run: Gemini API not called"],
                model=model,
                tile=crop.tile_path.name,
                crop_box=crop.crop_box,
                osm_polygon_crop_px=crop.osm_polygon_crop_px,
                experiment_version=EXPERIMENT_VERSION,
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
            "stage1_roof_shape": row.get("roof_shape"),
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
