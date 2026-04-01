"""
MVP Coordinate Analysis Tool - Raising Rooves

Given a lat/lon coordinate, queries building footprint polygons for the
surrounding tile area, downloads the satellite tile, and saves an annotated
image showing each building outline with its area label.

Data source: OpenStreetMap Overpass API (no key, no download required).
Local alternative: Microsoft Australia Building Footprints GeoJSON file
(download from https://github.com/microsoft/AustraliaBuildingFootprints,
then pass --footprint-file path/to/file.geojson).

Usage:
    # By coordinate:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185

    # By suburb name (uses centroid from config):
    python -m tools.analyse_coordinate --suburb Clayton

    # With local MS Building Footprints file:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --footprint-file data/raw/footprints/australia.geojson

    # Debug logging:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug

Outputs (saved to data/output/):
    - <tag>_annotated.png   : satellite tile with coloured building polygon overlays
    - <tag>_buildings.csv   : per-building area, centroid lat/lon, source
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

# ── Project imports ───────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import (
    DEFAULT_TILE_SIZE,
    DEFAULT_ZOOM,
    GOOGLE_MAPS_API_KEY,
    GOOGLE_MAPS_BASE_URL,
    OUTPUT_DIR,
    TILES_DIR,
)
from shared.file_io import ensure_dir
from shared.geo_utils import latlon_to_tile, tile_centre_latlon
from shared.logging_config import setup_logging
from shared.validation import validate_env_vars
from stage1_segmentation.building_footprint_segmenter import (
    FootprintQueryResult,
    query_buildings_in_tile,
)

logger = setup_logging("analyse_coordinate")

# ── Colour palette ────────────────────────────────────────────────────────────
_COLOURS = [
    (0, 200, 255),
    (0, 255, 120),
    (255, 80,  80),
    (255, 0,  200),
    (0,  180,   0),
    (180,  0, 255),
    (0,  255, 255),
    (255, 140,   0),
    (200, 200,   0),
    (0,  100, 255),
]


# ── Tile download ─────────────────────────────────────────────────────────────


def _download_tile(lat: float, lon: float, zoom: int, save_path: Path) -> bool:
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
            logger.warning(
                "Tile download attempt %d failed: %s -- retrying in %.1fs",
                attempt, exc, wait,
            )
            time.sleep(wait)
    return False


def download_tile(lat: float, lon: float, zoom: int, tile_dir: Path) -> tuple | None:
    """Download a single tile. Returns (path, tile_lat, tile_lon) or None on failure."""
    ensure_dir(tile_dir)
    tx, ty = latlon_to_tile(lat, lon, zoom)
    tlat, tlon = tile_centre_latlon(tx, ty, zoom)
    fname = f"coord_{zoom}_{tx}_{ty}.png"
    fpath = tile_dir / fname
    if fpath.exists() and fpath.stat().st_size > 0:
        logger.debug("Tile cached: %s", fname)
    else:
        ok = _download_tile(tlat, tlon, zoom, fpath)
        if not ok:
            logger.error("Failed to download tile for (%.5f, %.5f)", lat, lon)
            return None
    return fpath, tlat, tlon


# ── Annotation ────────────────────────────────────────────────────────────────


def annotate_tile(tile_path: Path, result: FootprintQueryResult) -> np.ndarray:
    """
    Draw coloured building polygon overlays on the satellite tile.
    Each polygon label shows the building area in m2.
    """
    img = cv2.imread(str(tile_path))
    if img is None:
        img = np.zeros((DEFAULT_TILE_SIZE, DEFAULT_TILE_SIZE, 3), dtype=np.uint8)

    overlay = img.copy()

    for i, bldg in enumerate(result.buildings):
        if not bldg.polygon or len(bldg.polygon) < 3:
            continue

        colour = _COLOURS[i % len(_COLOURS)]
        pts = np.array([[p[0], p[1]] for p in bldg.polygon], dtype=np.int32)
        pts = np.clip(pts, 0, DEFAULT_TILE_SIZE - 1)

        cv2.fillPoly(overlay, [pts], colour)
        cv2.polylines(img, [pts], isClosed=True, color=colour, thickness=2)

        cx = int(pts[:, 0].mean())
        cy = int(pts[:, 1].mean())
        label = f"{bldg.area_m2:.0f}m2"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.4
        thickness = 1
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        tx_label = max(0, cx - tw // 2)
        ty_label = max(th + 4, cy)
        cv2.rectangle(
            img,
            (tx_label - 2, ty_label - th - 2),
            (tx_label + tw + 2, ty_label + baseline),
            (0, 0, 0), -1,
        )
        cv2.putText(img, label, (tx_label, ty_label), font, font_scale, colour, thickness, cv2.LINE_AA)

    cv2.addWeighted(overlay, 0.25, img, 0.75, 0, img)
    return img


# ── Summary ───────────────────────────────────────────────────────────────────


def print_summary(result: FootprintQueryResult, tag: str) -> str:
    """Format and print a summary table. Returns the text."""
    tile_area = DEFAULT_TILE_SIZE * DEFAULT_TILE_SIZE * (0.298 ** 2)  # approx m2 at zoom 19
    coverage_pct = (result.total_area_m2 / tile_area * 100) if tile_area > 0 else 0

    lines = [
        "",
        "=" * 60,
        f"  Raising Rooves - Building Footprint Analysis: {tag}",
        "=" * 60,
        f"  Source           : OSM Overpass API (OpenStreetMap)",
        f"  Buildings found  : {result.count}",
        f"  Total roof area  : {result.total_area_m2:,.0f} m2",
        f"  Approx tile area : {tile_area:,.0f} m2",
        f"  Roof coverage    : {coverage_pct:.1f} %",
        "-" * 60,
    ]

    if result.buildings:
        lines.append(f"  {'#':<5} {'Building ID':<15} {'Area (m2)':>10}  {'Source'}")
        lines.append(f"  {'-'*5} {'-'*15} {'-'*10}  {'-'*6}")
        for i, bldg in enumerate(sorted(result.buildings, key=lambda b: -b.area_m2)):
            lines.append(
                f"  {i+1:<5} {bldg.building_id:<15} {bldg.area_m2:>10,.1f}  {bldg.source}"
            )

    lines.append("=" * 60)
    text = "\n".join(lines)
    print(text)
    return text


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raising Rooves -- Building Footprint Coordinate Analysis"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lat", type=float, help="Latitude (decimal degrees)")
    group.add_argument("--suburb", type=str, help="Suburb name from config")
    parser.add_argument("--lon", type=float, help="Longitude (required with --lat)")
    parser.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    parser.add_argument(
        "--footprint-file",
        type=Path,
        default=None,
        help="Optional: path to local GeoJSON footprint file (e.g. Microsoft AU Building Footprints). "
             "If omitted, queries OSM Overpass API.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    level = "DEBUG" if args.debug else "INFO"
    setup_logging("analyse_coordinate", level=level)

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

    validate_env_vars(["GOOGLE_MAPS_API_KEY"])

    tile_dir = ensure_dir(TILES_DIR / "coordinate_analysis")
    out_dir = ensure_dir(OUTPUT_DIR)

    # Step 1: Download satellite tile (background visual only)
    tile_result = download_tile(lat, lon, args.zoom, tile_dir)
    if tile_result is None:
        logger.error("No tile downloaded. Check GOOGLE_MAPS_API_KEY.")
        sys.exit(1)
    tile_path, tile_lat, tile_lon = tile_result
    logger.info("Tile: %s (centre %.5f, %.5f)", tile_path.name, tile_lat, tile_lon)

    # Step 2: Query building footprints
    source_label = f"local file: {args.footprint_file}" if args.footprint_file else "OSM Overpass API"
    logger.info("Querying building footprints via %s...", source_label)
    try:
        footprint_result = query_buildings_in_tile(
            centre_lat=tile_lat,
            centre_lon=tile_lon,
            zoom=args.zoom,
            local_file=args.footprint_file,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # Step 3: Print summary
    summary_text = print_summary(footprint_result, tag)

    # Step 4: Save outputs
    img = annotate_tile(tile_path, footprint_result)
    img_path = out_dir / f"{tag}_annotated.png"
    cv2.imwrite(str(img_path), img)
    logger.info("Annotated image -> %s", img_path)

    csv_path = out_dir / f"{tag}_buildings.csv"
    if footprint_result.buildings:
        with open(csv_path, "w", newline="") as f:
            fieldnames = ["building_id", "area_m2", "source", "centroid_lon", "centroid_lat"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for bldg in footprint_result.buildings:
                lons = [c[0] for c in bldg.polygon_latlon]
                lats = [c[1] for c in bldg.polygon_latlon]
                writer.writerow({
                    "building_id": bldg.building_id,
                    "area_m2": bldg.area_m2,
                    "source": bldg.source,
                    "centroid_lon": round(sum(lons) / len(lons), 6),
                    "centroid_lat": round(sum(lats) / len(lats), 6),
                })
        logger.info("Buildings CSV -> %s", csv_path)

    summary_path = out_dir / f"{tag}_summary.txt"
    summary_path.write_text(summary_text)
    logger.info("Summary -> %s", summary_path)

    print(f"\nOutputs saved to: {out_dir}")
    print(f"  Annotated image : {img_path.name}")
    if footprint_result.buildings:
        print(f"  Buildings CSV   : {csv_path.name}")
    print(f"  Summary text    : {summary_path.name}")


if __name__ == "__main__":
    main()
