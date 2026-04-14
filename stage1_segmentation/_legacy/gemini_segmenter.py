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
import re
import time
from collections import Counter
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

GEMINI_MODEL = "models/gemini-2.5-flash"
# Fallback models tried in order when the primary hits a quota wall or 404.
# Must be valid names as returned by client.models.list().
GEMINI_FALLBACK_MODELS = []
GEMINI_RPM_FREE = 15                    # requests per minute on free tier
GEMINI_RPM_DELAY = 60.0 / GEMINI_RPM_FREE  # ~4 s between calls, free tier
MAX_RETRIES = 4
RETRY_BACKOFF = 2.0
DEFAULT_RATE_LIMIT_WAIT = 65.0          # fallback wait (s) when 429 gives no hint
MIN_SEGMENT_PIXELS = 300                # discard fragments smaller than this (~6x6 m at zoom 19)

# ── Structured prompt ────────────────────────────────────────────────────────

_PROMPT = """
You are an expert building analyst examining a top-down satellite image of Melbourne, Australia.
Resolution: ~0.3 m/pixel. Image size: 640x640 pixels (covers ~190x190 metres).

TASK: Detect and outline every building rooftop you can actually SEE in the image.

ANTI-HALLUCINATION RULES (MOST IMPORTANT):
- Only trace rooftops that are VISUALLY PRESENT — distinct shapes with visible edges.
- NEVER output identical or near-identical polygons. Every polygon must be a different building.
- NEVER fill the image with a regular grid of same-sized boxes. This is wrong.
- If you are uncertain whether something is a roof, omit it. Precision > recall.
- Do NOT output more polygons than there are clearly visible buildings.

WHAT TO DETECT:
1. LARGE FLAT COMMERCIAL ROOFS: uniform white/cream/grey rectangles — often the most
   prominent feature. Include these even if they look featureless.
2. RESIDENTIAL ROOFS: terracotta (red/brown), concrete tile (grey), or metal — typically
   smaller, may be angled, can appear at any rotation.
3. INDUSTRIAL/WAREHOUSE: corrugated metal, long narrow rectangles.

WHAT TO EXCLUDE: roads, car parks, footpaths, bare ground, trees, shadows, cars.

FOR EACH VISIBLE ROOF return one JSON object:
  "polygon": [[x,y], ...] — EXACTLY 4 to 6 integer pixel vertices (0-639). Use a simple
             rectangular or trapezoidal outline. DO NOT use staircase traces.
  "material": "metal" | "tile" | "concrete" | "unknown"
  "colour": "light" | "dark" | "red" | "grey" | "blue" | "green" | "brown" | "unknown"
  "confidence": float 0.0-1.0 (your certainty this is a real roof)

Return ONLY a valid JSON array (no markdown, no explanation). If no roofs visible, return [].
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


def _parse_retry_delay(exc: Exception) -> float:
    """
    Extract the API-suggested retry delay (seconds) from a 429 exception.

    The Gemini API embeds a retryDelay field like ``'retryDelay': '53s'``
    in the error details.  Fall back to DEFAULT_RATE_LIMIT_WAIT if not found.
    """
    match = re.search(r"retryDelay.*?(\d+)s", str(exc))
    return float(match.group(1)) + 5 if match else DEFAULT_RATE_LIMIT_WAIT


def _is_quota_error(exc: Exception) -> bool:
    """Return True if the exception is a rate-limit / quota exhaustion error."""
    s = str(exc)
    return "429" in s or "RESOURCE_EXHAUSTED" in s


def _is_model_not_found(exc: Exception) -> bool:
    """Return True for 404 model-unavailable errors (skip to next fallback immediately)."""
    s = str(exc)
    return "404" in s and ("NOT_FOUND" in s or "no longer available" in s or "not found" in s.lower())


def _is_auth_error(exc: Exception) -> bool:
    """Return True for unrecoverable 400-level auth errors (expired/invalid key).

    Deliberately excludes 429 quota errors — those are retryable.
    """
    s = str(exc)
    is_400 = "400 " in s or "'code': 400" in s
    return is_400 and ("API_KEY_INVALID" in s or "API key expired" in s)


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
        try:
            pts = np.array([[p[0], p[1]] for p in poly], dtype=np.int32)
        except (TypeError, IndexError):
            continue  # skip malformed vertices
        pts = np.clip(pts, 0, size - 1)
        cv2.fillPoly(canvas, [pts], 255)
    return canvas > 127


def _parse_response(raw: str) -> list[dict]:
    """
    Parse Gemini's JSON response, tolerating truncation and code fences.

    If the full JSON fails to parse (e.g. response was cut off mid-array),
    salvages all complete objects found before the truncation point.

    Returns a list of roof dicts, or [] on unrecoverable failure.
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
    except json.JSONDecodeError:
        pass  # fall through to recovery

    # Salvage partial response: find the last complete JSON object in the array
    last_obj_end = text.rfind("},")
    if last_obj_end > 0:
        partial = text[:last_obj_end + 1] + "]"
        try:
            data = json.loads(partial)
            if isinstance(data, list) and data:
                logger.warning(
                    "Response truncated -- salvaged %d roofs from partial JSON", len(data)
                )
                return data
        except json.JSONDecodeError:
            pass

    logger.warning("JSON parse failed entirely | raw: %.300s", raw)
    return []


# ── Public API ────────────────────────────────────────────────────────────────


def segment_tile(
    tile_path: Path,
    client: genai.Client,
) -> GeminiSegmentationResult:
    """
    Segment a single 640x640 satellite tile with Gemini.

    Sends the image to the Gemini Vision API and converts the returned
    roof polygons into a binary mask plus per-segment metadata.

    Retries up to MAX_RETRIES times.  On 429 / RESOURCE_EXHAUSTED errors the
    wait time is taken from the API-suggested retryDelay (typically 50-65 s)
    rather than a short exponential backoff.  If the primary model is
    quota-exhausted after all retries, falls back through GEMINI_FALLBACK_MODELS.

    Args:
        tile_path: Path to the tile PNG.
        client: Configured Gemini Client (from _get_client()).

    Returns:
        GeminiSegmentationResult with mask and segment list.
    """
    image = Image.open(tile_path).convert("RGB")
    h, w = image.height, image.width
    image_part = _image_to_part(image)

    models_to_try = [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS

    for model_name in models_to_try:
        raw = ""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[image_part, types.Part.from_text(text=_PROMPT)],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=3000,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                raw = response.text
                if model_name != GEMINI_MODEL:
                    logger.info("Used fallback model %s for %s", model_name, tile_path.name)
                break  # success
            except Exception as exc:  # noqa: BLE001
                if _is_auth_error(exc):
                    # No point retrying — key is invalid/expired across all models
                    raise RuntimeError(
                        f"Gemini API key is invalid or expired: {exc}\n"
                        "Get a new key at https://aistudio.google.com/app/apikey "
                        "and update GEMINI_API_KEY in your .env file."
                    ) from exc
                if _is_model_not_found(exc):
                    logger.warning(
                        "Model %s not available for this API key -- trying next model",
                        model_name,
                    )
                    break  # skip immediately to next fallback
                if _is_quota_error(exc):
                    wait = _parse_retry_delay(exc)
                    if attempt == MAX_RETRIES:
                        logger.warning(
                            "Quota exhausted on %s after %d attempts -- trying next model",
                            model_name, MAX_RETRIES,
                        )
                        break  # try next model
                    logger.warning(
                        "[%s] attempt %d/%d rate-limited for %s -- waiting %.0fs",
                        model_name, attempt, MAX_RETRIES, tile_path.name, wait,
                    )
                else:
                    wait = RETRY_BACKOFF ** attempt
                    if attempt == MAX_RETRIES:
                        logger.error(
                            "Gemini error after %d attempts for %s: %s",
                            MAX_RETRIES, tile_path.name, exc,
                        )
                        return GeminiSegmentationResult(
                            tile_path=tile_path,
                            mask=np.zeros((h, w), dtype=bool),
                        )
                    logger.warning(
                        "[%s] attempt %d/%d failed for %s: %s -- retrying in %.1fs",
                        model_name, attempt, MAX_RETRIES, tile_path.name, exc, wait,
                    )
                time.sleep(wait)

        if raw:
            break  # got a response, skip remaining fallback models
    else:
        logger.error("All Gemini models exhausted for %s", tile_path.name)
        return GeminiSegmentationResult(
            tile_path=tile_path,
            mask=np.zeros((h, w), dtype=bool),
        )

    roof_dicts = _parse_response(raw)

    # ── Pass 1: compute pixel counts, apply size filters ─────────────────────
    tile_pixels = DEFAULT_TILE_SIZE * DEFAULT_TILE_SIZE
    candidates: list[tuple[int, dict, list, int]] = []  # (original_idx, roof, poly, px)

    for i, roof in enumerate(roof_dicts):
        poly = roof.get("polygon", [])
        if not poly or len(poly) < 3:
            continue
        single_mask = _polygons_to_mask([poly], size=DEFAULT_TILE_SIZE)
        pixel_count = int(single_mask.sum())
        if pixel_count < MIN_SEGMENT_PIXELS:
            continue
        if pixel_count > tile_pixels * 0.35:
            logger.debug(
                "Discarding oversized polygon: %d px (%.0f%% of tile)",
                pixel_count, pixel_count / tile_pixels * 100,
            )
            continue
        candidates.append((i, roof, poly, pixel_count))

    # ── Pass 2: hallucination filters ────────────────────────────────────────
    if candidates:
        # Filter A: identical pixel-count clusters (> 3 occurrences) — the
        # model fills a regular grid of same-sized boxes.
        px_counts = Counter(px for _, _, _, px in candidates)
        bad_px = {px for px, n in px_counts.items() if n > 2}
        if bad_px:
            n_before = len(candidates)
            candidates = [(i, r, p, px) for i, r, p, px in candidates if px not in bad_px]
            logger.warning(
                "%s: dropped %d hallucinated identical-size polygons "
                "(repeated pixel counts: %s)",
                tile_path.name, n_before - len(candidates), sorted(bad_px),
            )

        # Filter B: all remaining roofs have uniform confidence — still a grid.
        if len(candidates) > 10:
            confs = [r.get("confidence", 0) for _, r, _, _ in candidates]
            if max(confs) - min(confs) < 0.01:
                logger.warning(
                    "%s: %d roofs with identical confidence (%.2f) -- discarding",
                    tile_path.name, len(candidates), confs[0],
                )
                candidates = []

    # ── Build segment objects ─────────────────────────────────────────────────
    polygons_for_mask: list[list[list[int]]] = []
    segments: list[GeminiRoofSegment] = []

    for i, roof, poly, pixel_count in candidates:
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
        "%s -> %d roofs | %d roof pixels",
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
