"""
MVP Coordinate Analysis Tool — Raising Rooves

Given a lat/lon coordinate, downloads satellite tiles, uses Gemini to
outline every visible roof, calculates each roof's area (m²), and saves
an annotated image showing outlines + stats.

Usage:
    # By coordinate:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185

    # By suburb name (uses centroid from config):
    python -m tools.analyse_coordinate --suburb Clayton

    # Larger area (3×3 tile grid):
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --grid 3

    # Debug logging:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug

Outputs (saved to data/output/):
    - <tag>_annotated.png   : satellite image with coloured roof polygons
    - <tag>_roofs.csv       : per-roof area, material, colour, confidence
    - <tag>_summary.txt     : totals printed to console and saved to file
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

# ── Project imports ───────────────────────────────────────────────────────────

# Allow running as  python -m tools.analyse_coordinate  from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import (
    DEFAULT_TILE_SIZE,
    DEFAULT_ZOOM,
    GOOGLE_MAPS_API_KEY,
    GOOGLE_MAPS_BASE_URL,
    GEMINI_API_KEY,
    OUTPUT_DIR,
    TILES_DIR,
)
from shared.file_io import ensure_dir
from shared.geo_utils import latlon_to_tile, tile_centre_latlon, pixels_to_area_m2
from shared.logging_config import setup_logging
from shared.validation import validate_env_vars
from stage1_segmentation.gemini_segmenter import (
    GeminiSegmentationResult,
    _get_model,
    segment_tile,
)

logger = setup_logging("analyse_coordinate")

# ── Colour palette for roof polygon overlays ─────────────────────────────────
# BGR tuples for OpenCV drawing; enough distinct colours for ~20 roofs/tile
_COLOURS = [
    (0, 200, 255),    # amber
    (0, 255, 120),    # green
    (255, 80,  80),   # blue
    (255, 0,  200),   # magenta
    (0,  180, 0),     # dark green
    (180, 0,  255),   # purple
    (0,  255, 255),   # yellow
    (255, 140, 0),    # teal
    (200, 200, 0),    # cyan
    (0,  100, 255),   # orange
]


# ── Tile download (re-used from tile_downloader but self-contained here) ─────


def _download_tile(lat: float, lon: float, zoom: int, save_path: Path) -> bool:
    """Download a single Google Maps Static tile. Returns True on success."""
    url = (
        f"{GOOGLE_MAPS_BASE_URL}"
        f"?center={lat},{lon}&zoom={zoom}"
        f"&size={DEFAULT_TILE_SIZE}x{DEFAULT_TILE_SIZE}"
        f"&maptype=satellite"
        f"&key={GOOGLE_MAPS_API_KEY}"
    )
    for attempt in range(1, 4):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            if "image" not in r.headers.get("Content-Type", ""):
                logger.warning("Non-image response for tile at (%f, %f)", lat, lon)
                return False
            save_path.write_bytes(r.content)
            return True
        except requests.RequestException as exc:
            wait = 2.0 ** attempt
            logger.warning("Tile download attempt %d failed: %s — retrying in %.1fs", attempt, exc, wait)
            time.sleep(wait)
    return False


def download_grid(
    centre_lat: float,
    centre_lon: float,
    zoom: int,
    grid: int,
    tile_dir: Path,
) -> list[tuple[Path, float, float]]:
    """
    Download an N×N grid of satellite tiles centred on the given coordinate.

    Args:
        centre_lat, centre_lon: Centre coordinate.
        zoom: Zoom level (default 19).
        grid: Side length of tile grid (1 = single tile, 3 = 3×3 = 9 tiles).
        tile_dir: Directory to save tiles into.

    Returns:
        List of (tile_path, tile_centre_lat, tile_centre_lon) tuples.
    """
    ensure_dir(tile_dir)
    cx, cy = latlon_to_tile(centre_lat, centre_lon, zoom)
    offset = grid // 2
    tiles = []

    total = grid * grid
    logger.info("Downloading %d×%d = %d tile(s) around (%.5f, %.5f)", grid, grid, total, centre_lat, centre_lon)

    for dy in range(-offset, offset + 1):
        for dx in range(-offset, offset + 1):
            tx, ty = cx + dx, cy + dy
            tlat, tlon = tile_centre_latlon(tx, ty, zoom)
            fname = f"coord_{zoom}_{tx}_{ty}.png"
            fpath = tile_dir / fname
            if fpath.exists() and fpath.stat().st_size > 0:
                logger.debug("Tile cached: %s", fname)
            else:
                ok = _download_tile(tlat, tlon, zoom, fpath)
                if not ok:
                    logger.warning("Skipping failed tile %s", fname)
                    continue
                time.sleep(0.1)   # gentle rate limiting
            tiles.append((fpath, tlat, tlon))

    logger.info("Downloaded / cached %d tiles", len(tiles))
    return tiles


# ── Annotation drawing ────────────────────────────────────────────────────────


def annotate_tile(
    tile_path: Path,
    result: GeminiSegmentationResult,
    tile_lat: float,
    zoom: int,
) -> np.ndarray:
    """
    Draw coloured polygon outlines + area labels on a satellite tile.

    Args:
        tile_path: Original tile PNG.
        result: Gemini segmentation result for this tile.
        tile_lat: Latitude of tile centre (for m² calculation).
        zoom: Zoom level.

    Returns:
        Annotated image as a NumPy BGR array (OpenCV format).
    """
    img = cv2.imread(str(tile_path))
    if img is None:
        img = np.zeros((DEFAULT_TILE_SIZE, DEFAULT_TILE_SIZE, 3), dtype=np.uint8)

    overlay = img.copy()

    for i, seg in enumerate(result.segments):
        colour = _COLOURS[i % len(_COLOURS)]
        pts = np.array([[p[0], p[1]] for p in seg.polygon], dtype=np.int32)
        pts = np.clip(pts, 0, DEFAULT_TILE_SIZE - 1)

        # Filled semi-transparent polygon
        cv2.fillPoly(overlay, [pts], colour)

        # Solid outline
        cv2.polylines(img, [pts], isClosed=True, color=colour, thickness=2)

        # Area label at centroid
        area_m2 = pixels_to_area_m2(seg.pixel_count, tile_lat, zoom)
        cx, cy = int(seg.centroid[0]), int(seg.centroid[1])
        label = f"{area_m2:.0f}m2"
        cv2.putText(img, label, (cx - 15, cy), cv2.FONT_HERSHEY_SIMPLEX,
                    0.35, (255, 255, 255), 1, cv2.LINE_AA)

    # Blend overlay at 30% opacity
    cv2.addWeighted(overlay, 0.30, img, 0.70, 0, img)
    return img


# ── Stats helpers ─────────────────────────────────────────────────────────────


def compute_stats(
    results: list[tuple[GeminiSegmentationResult, float, int]],
) -> dict:
    """
    Compute per-roof and summary area statistics.

    Args:
        results: List of (GeminiSegmentationResult, tile_lat, zoom) tuples.

    Returns:
        Dict with keys: roofs (list of dicts), total_roof_m2, total_tile_m2,
        coverage_pct, num_roofs.
    """
    roofs = []
    total_roof_px = 0
    total_tile_px = 0

    for result, lat, zoom in results:
        tile_px = DEFAULT_TILE_SIZE * DEFAULT_TILE_SIZE
        total_tile_px += tile_px
        total_roof_px += int(result.mask.sum())

        for seg in result.segments:
            area = pixels_to_area_m2(seg.pixel_count, lat, zoom)
            roofs.append({
                "roof_id": f"roof_{seg.segment_id:03d}",
                "area_m2": round(area, 1),
                "pixel_count": seg.pixel_count,
                "material": seg.material,
                "colour": seg.colour,
                "confidence": round(seg.confidence, 2),
                "centroid_x": round(seg.centroid[0], 1),
                "centroid_y": round(seg.centroid[1], 1),
            })

    # Use a representative lat for total tile area (first result)
    rep_lat, rep_zoom = results[0][1], results[0][2]
    total_tile_m2 = pixels_to_area_m2(total_tile_px, rep_lat, rep_zoom)
    total_roof_m2 = pixels_to_area_m2(total_roof_px, rep_lat, rep_zoom)
    coverage_pct = (total_roof_m2 / total_tile_m2 * 100) if total_tile_m2 > 0 else 0.0

    return {
        "roofs": roofs,
        "total_roof_m2": round(total_roof_m2, 1),
        "total_tile_m2": round(total_tile_m2, 1),
        "coverage_pct": round(coverage_pct, 1),
        "num_roofs": len(roofs),
    }


def print_summary(stats: dict, tag: str) -> str:
    """Format and print a summary table. Returns the text."""
    lines = [
        "",
        "=" * 52,
        f"  Raising Rooves — Coordinate Analysis: {tag}",
        "=" * 52,
        f"  Roofs detected    : {stats['num_roofs']}",
        f"  Total roof area   : {stats['total_roof_m2']:,.0f} m²",
        f"  Total tile area   : {stats['total_tile_m2']:,.0f} m²",
        f"  Roof coverage     : {stats['coverage_pct']:.1f} %",
        "-" * 52,
    ]
    if stats["roofs"]:
        lines.append(f"  {'Roof':<10} {'Area (m²)':>9}  {'Material':<10}  {'Colour':<8}  {'Conf':>5}")
        lines.append(f"  {'-'*10} {'-'*9}  {'-'*10}  {'-'*8}  {'-'*5}")
        for r in sorted(stats["roofs"], key=lambda x: -x["area_m2"]):
            lines.append(
                f"  {r['roof_id']:<10} {r['area_m2']:>9,.1f}  {r['material']:<10}  {r['colour']:<8}  {r['confidence']:>5.2f}"
            )
    lines.append("=" * 52)
    text = "\n".join(lines)
    print(text)
    return text


# ── Main entry point ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raising Rooves — Coordinate Analysis (Gemini Vision MVP)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lat", type=float, help="Latitude (decimal degrees, e.g. -37.9261)")
    group.add_argument("--suburb", type=str, help="Suburb name from config (e.g. 'Clayton')")
    parser.add_argument("--lon", type=float, help="Longitude (required with --lat)")
    parser.add_argument("--zoom", type=int, default=DEFAULT_ZOOM, help=f"Zoom level (default {DEFAULT_ZOOM})")
    parser.add_argument("--grid", type=int, default=1, choices=[1, 3, 5],
                        help="Tile grid size: 1=single tile, 3=3×3 grid (default 1)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = "DEBUG" if args.debug else "INFO"
    setup_logging("analyse_coordinate", level=level)

    # Resolve coordinate
    if args.suburb:
        from config.suburbs import get_suburb
        suburb = get_suburb(args.suburb)
        lat = (suburb.bbox[0] + suburb.bbox[2]) / 2
        lon = (suburb.bbox[1] + suburb.bbox[3]) / 2
        tag = args.suburb.lower().replace(" ", "_")
        logger.info("Using centroid of %s: (%.5f, %.5f)", args.suburb, lat, lon)
    else:
        if args.lon is None:
            parser.error("--lon is required when using --lat")
        lat, lon = args.lat, args.lon
        tag = f"{lat:.5f}_{lon:.5f}"

    validate_env_vars(["GOOGLE_MAPS_API_KEY", "GEMINI_API_KEY"])

    # Directories
    tile_dir = ensure_dir(TILES_DIR / "coordinate_analysis")
    out_dir = ensure_dir(OUTPUT_DIR)

    # Step 1: Download tiles
    tiles = download_grid(lat, lon, args.zoom, args.grid, tile_dir)
    if not tiles:
        logger.error("No tiles downloaded. Check your GOOGLE_MAPS_API_KEY.")
        sys.exit(1)

    # Step 2: Segment with Gemini
    model = _get_model()
    all_results: list[tuple[GeminiSegmentationResult, float, int]] = []
    annotated_frames = []
    rate_delay = 60.0 / 15  # free-tier safe default (~4s)

    for i, (tile_path, tile_lat, tile_lon) in enumerate(tiles):
        logger.info("[%d/%d] Segmenting %s with Gemini...", i + 1, len(tiles), tile_path.name)
        result = segment_tile(tile_path, model)
        all_results.append((result, tile_lat, args.zoom))

        # Annotate this tile
        annotated = annotate_tile(tile_path, result, tile_lat, args.zoom)
        annotated_frames.append(annotated)

        if i < len(tiles) - 1:
            time.sleep(rate_delay)

    # Step 3: Compute stats
    stats = compute_stats(all_results)
    summary_text = print_summary(stats, tag)

    # Step 4: Save outputs
    #  a) Annotated image (stitch tiles if grid > 1)
    if len(annotated_frames) == 1:
        final_img = annotated_frames[0]
    else:
        # Stitch into grid
        grid_side = args.grid
        rows = []
        for row_i in range(grid_side):
            row_frames = annotated_frames[row_i * grid_side: (row_i + 1) * grid_side]
            rows.append(np.hstack(row_frames))
        final_img = np.vstack(rows)

    img_path = out_dir / f"{tag}_annotated.png"
    cv2.imwrite(str(img_path), final_img)
    logger.info("Annotated image saved → %s", img_path)

    #  b) Per-roof CSV
    csv_path = out_dir / f"{tag}_roofs.csv"
    if stats["roofs"]:
        fieldnames = list(stats["roofs"][0].keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(stats["roofs"])
        logger.info("Roof CSV saved     → %s", csv_path)

    #  c) Summary text
    summary_path = out_dir / f"{tag}_summary.txt"
    summary_path.write_text(summary_text)
    logger.info("Summary saved      → %s", summary_path)

    print(f"\nOutputs saved to:  {out_dir}")
    print(f"  Annotated image:  {img_path.name}")
    if stats["roofs"]:
        print(f"  Roof data CSV:    {csv_path.name}")
    print(f"  Summary text:     {summary_path.name}")


if __name__ == "__main__":
    main()
