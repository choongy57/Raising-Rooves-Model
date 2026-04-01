"""
Gemini Vision-based roof segmenter for the Raising Rooves pipeline.

Replaces the SAM3/Colab workflow entirely.  No GPU required — each tile
is sent to the Gemini 2.0 Flash API; the model returns per-roof JSON with
polygon coordinates, material, colour, and confidence.

Polygons are rendered into binary masks using OpenCV and saved in the
same mask format expected by the rest of the pipeline.

Rate limits (Gemini 2.0 Flash, as of 2025):
    Free tier  : 15 RPM   → 2208 tiles ≈ 2.5 hours
    Paid tier  : 2000 RPM → 2208 tiles ≈ 1.5 minutes

The run is checkpoint-aware: already-processed tiles are skipped, so
interrupted runs can be safely resumed.
"""

import io
import json
import time
from pathlib import Path
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image
from google import genai
from google.genai import types

from config.settings import GEMINI_API_KEY, MASKS_DIR, DEFAULT_TILE_SIZE
from shared.file_io import ensure_dir
from shared.logging_config import setup_logging

logger = setup_logging("gemini_segmenter")

# ── Constants ────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.0-flash"
GEMINI_RPM_FREE = 15                    # requests per minute on free tier
GEMINI_RPM_DELAY = 60.0 / GEMINI_RPM_FREE  # ~4 s between calls, free tier
MAX_RETRIES = 5
RETRY_BACKOFF = 2.0
MIN_SEGMENT_PIXELS = 50                 # discard fragments smaller than this

# ── Structured prompt ────────────────────────────────────────────────────────

_PROMPT = """
You are analysing a satellite image of a Melbourne suburb (640×640 px, ~0.3 m/pixel).

Identify EVERY visible rooftop. For each roof return:
  - "polygon": [[x,y], ...] — 6–20 integer pixel vertices (0–639) tracing the roof outline
  - "material": one of "metal", "tile", "concrete", "unknown"
  - "colour": one of "light", "dark", "red", "grey", "blue", "green", "brown", "unknown"
  - "confidence": 0.0–1.0

Return ONLY a valid JSON array. If no roofs are visible return [].
"""

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class GeminiRoofSegment:
    """A single roof detected by Gemini, with polygon and classification."""

    segment_id: int
    polygon: list[list[int]]            # [[x, y], ...]
    pixel_count: int
    bbox: tuple[int, int, int, int]     # (x_min, y_min, x_max, y_max)
    centroid: tuple[float, float]       # (cx, cy) in pixels
    material: str
    colour: str
    confidence: float


@dataclass
class GeminiSegmentationResult:
    """Full segmentation result for one tile."""

    tile_path: Path
    mask: np.ndarray                    # binary (H, W), True = roof pixel
    segments: list[GeminiRoofSegment] = field(default_factory=list)


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_client() -> genai.Client:
    """Create and return a configured Gemini API client."""
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY is not set.\n"
            "1. Get a free key at https://aistudio.google.com/app/apikey\n"
            "2. Add  GEMINI_API_KEY=your_key  to your .env file"
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _image_to_part(image: Image.Image) -> types.Part:
    """Convert a PIL Image to a Gemini API Part (JPEG bytes)."""
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


def _polygons_to_mask(
    polygons: list[list[list[int]]],
    size: int = DEFAULT_TILE_SIZE,
) -> np.ndarray:
    """
    Render a list of polygon vertex lists into a binary NumPy mask.

    Args:
        polygons: Each polygon is a list of [x, y] integer pairs.
        size: Image side length in pixels (square tiles assumed).

    Returns:
        Boolean mask (H, W) — True where any roof polygon covers the pixel.
    """
    canvas = np.zeros((size, size), dtype=np.uint8)
    for poly in polygons:
        if len(poly) < 3:
            continue
        pts = np.array([[p[0], p[1]] for p in poly], dtype=np.int32)
        pts = np.clip(pts, 0, size - 1)
        cv2.fillPoly(canvas, [pts], 255)
    return canvas > 127


def _parse_response(raw: str) -> list[dict]:
    """
    Parse Gemini's JSON response, tolerating minor formatting issues.

    Returns a list of roof dicts, or [] on failure.
    """
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse failed: %s | raw: %.300s", exc, raw)
        return []


# ── Public API ────────────────────────────────────────────────────────────────


def segment_tile(
    tile_path: Path,
    client: genai.Client,
) -> GeminiSegmentationResult:
    """
    Segment a single 640×640 satellite tile with Gemini.

    Sends the image to the Gemini Vision API and converts the returned
    roof polygons into a binary mask plus per-segment metadata.

    Args:
        tile_path: Path to the tile PNG.
        client: Configured Gemini Client (from _get_client()).

    Returns:
        GeminiSegmentationResult with mask and segment list.
    """
    image = Image.open(tile_path).convert("RGB")
    h, w = image.height, image.width
    image_part = _image_to_part(image)

    raw = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[image_part, types.Part.from_text(text=_PROMPT)],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            raw = response.text
            break
        except Exception as exc:  # noqa: BLE001 — covers rate limits + transient errors
            wait = RETRY_BACKOFF ** attempt
            if attempt == MAX_RETRIES:
                logger.error(
                    "Gemini failed after %d attempts for %s: %s",
                    MAX_RETRIES, tile_path.name, exc,
                )
                return GeminiSegmentationResult(
                    tile_path=tile_path,
                    mask=np.zeros((h, w), dtype=bool),
                )
            logger.warning(
                "Attempt %d/%d for %s failed: %s — retrying in %.1fs",
                attempt, MAX_RETRIES, tile_path.name, exc, wait,
            )
            time.sleep(wait)

    roof_dicts = _parse_response(raw)
    polygons_for_mask: list[list[list[int]]] = []
    segments: list[GeminiRoofSegment] = []

    for i, roof in enumerate(roof_dicts):
        poly = roof.get("polygon", [])
        if not poly or len(poly) < 3:
            continue

        # Pixel count from filled polygon
        single_mask = _polygons_to_mask([poly], size=DEFAULT_TILE_SIZE)
        pixel_count = int(single_mask.sum())
        if pixel_count < MIN_SEGMENT_PIXELS:
            continue

        polygons_for_mask.append(poly)

        pts = np.array(poly)
        cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        x_min, y_min = int(pts[:, 0].min()), int(pts[:, 1].min())
        x_max, y_max = int(pts[:, 0].max()), int(pts[:, 1].max())

        segments.append(
            GeminiRoofSegment(
                segment_id=i,
                polygon=poly,
                pixel_count=pixel_count,
                bbox=(x_min, y_min, x_max, y_max),
                centroid=(cx, cy),
                material=str(roof.get("material", "unknown")),
                colour=str(roof.get("colour", "unknown")),
                confidence=float(roof.get("confidence", 0.8)),
            )
        )

    combined_mask = _polygons_to_mask(polygons_for_mask, size=DEFAULT_TILE_SIZE)
    logger.debug(
        "%s → %d roofs | %d roof pixels",
        tile_path.name, len(segments), int(combined_mask.sum()),
    )
    return GeminiSegmentationResult(
        tile_path=tile_path, mask=combined_mask, segments=segments
    )


def save_result(result: GeminiSegmentationResult, output_dir: Path) -> tuple[Path, Path]:
    """
    Save a segmentation result as a mask PNG + JSON sidecar to disk.

    This is compatible with the format expected by the Stage 1 pipeline
    (same as SAM-generated masks).

    Args:
        result: GeminiSegmentationResult to persist.
        output_dir: Directory to write files into.

    Returns:
        (mask_path, json_path) tuple.
    """
    ensure_dir(output_dir)
    stem = result.tile_path.stem

    mask_img = Image.fromarray((result.mask.astype(np.uint8) * 255), mode="L")
    mask_path = output_dir / f"{stem}_mask.png"
    mask_img.save(mask_path)

    metadata = {
        "tile": result.tile_path.name,
        "total_roof_pixels": int(result.mask.sum()),
        "num_segments": len(result.segments),
        "segments": [
            {
                "id": s.segment_id,
                "pixel_count": s.pixel_count,
                "bbox": list(s.bbox),
                "centroid": list(s.centroid),
                "material": s.material,
                "colour": s.colour,
                "confidence": s.confidence,
                "polygon": s.polygon,
            }
            for s in result.segments
        ],
    }
    json_path = output_dir / f"{stem}_meta.json"
    json_path.write_text(json.dumps(metadata, indent=2))

    return mask_path, json_path


def segment_suburb(
    tile_paths: list[Path],
    suburb_key: str,
    rate_limit_delay: float = GEMINI_RPM_DELAY,
) -> list[GeminiSegmentationResult]:
    """
    Segment all tiles for a suburb, saving results to disk as they complete.

    Checkpoint-aware: tiles whose mask + JSON sidecar already exist are
    skipped, so interrupted runs can be safely resumed.

    Args:
        tile_paths: Ordered list of tile PNG paths to segment.
        suburb_key: Suburb slug used for the mask output subdirectory
                    (e.g. "clayton").
        rate_limit_delay: Seconds to pause between API calls.  Default
                          is the free-tier safe rate (~4 s).

    Returns:
        List of GeminiSegmentationResult for newly processed tiles only
        (skipped tiles are not re-loaded).
    """
    client = _get_client()
    mask_dir = ensure_dir(MASKS_DIR / suburb_key)
    results: list[GeminiSegmentationResult] = []
    skipped = failed = processed = 0

    for idx, tile_path in enumerate(tile_paths):
        stem = tile_path.stem
        mask_path = mask_dir / f"{stem}_mask.png"
        json_path = mask_dir / f"{stem}_meta.json"

        if mask_path.exists() and json_path.exists():
            skipped += 1
            continue

        logger.info("[%d/%d] %s", idx + 1, len(tile_paths), tile_path.name)
        result = segment_tile(tile_path, client)

        if result.mask.sum() == 0:
            failed += 1
            logger.warning("No roofs detected in %s", tile_path.name)
        else:
            save_result(result, mask_dir)
            results.append(result)
            processed += 1

        if idx < len(tile_paths) - 1:
            time.sleep(rate_limit_delay)

    logger.info(
        "Segmentation done — %d processed, %d skipped (cached), %d no-roof tiles",
        processed, skipped, failed,
    )
    return results
