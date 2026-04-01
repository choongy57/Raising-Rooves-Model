"""
MVP Coordinate Analysis Tool - Raising Rooves

Given a lat/lon coordinate, queries building footprint polygons for the
surrounding area, downloads the satellite tile(s), and saves an annotated
image showing each building outline with its area label.

Data source: OpenStreetMap Overpass API (no key, no download required).
Local alternative: Microsoft Australia Building Footprints GeoJSON file
(download from https://github.com/microsoft/AustraliaBuildingFootprints,
then pass --footprint-file path/to/file.geojson).

Usage:
    # Single tile (~190x190m):
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185

    # 3x3 tile grid (~570x570m):
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --grid 3

    # 5x5 tile grid (~950x950m):
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --grid 5

    # By suburb name (uses centroid from config):
    python -m tools.analyse_coordinate --suburb Clayton

    # With local MS Building Footprints file:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --footprint-file data/raw/footprints/australia.geojson

    # Debug logging:
    python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug

Outputs (saved to data/output/):
    - <tag>_annotated.png   : satellite image(s) with coloured building polygon overlays
    - <tag>_buildings.csv   : per-building area, centroid lat/lon, source
    - <tag>_summary.txt     : totals printed to console and saved to file
"""

import argparse
import csv
import math
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
    query_buildings_in_bbox,
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


def download_grid(
    centre_lat: float,
    centre_lon: float,
    zoom: int,
    grid: int,
    tile_dir: Path,
) -> list[tuple[Path, float, float, int, int]]:
    """
    Download a grid x grid set of tiles centred on (centre_lat, centre_lon).

    Each 640px Google Maps image covers 640/256 = 2.5 standard map tile widths,
    so stepping by 1 tile unit causes 60% geographic overlap between adjacent
    images. We step by TILE_STEP=2 tile units so adjacent images share only a
    small ~38m overlap and the stitched result covers a genuinely larger area.

    Returns list of (path, tile_lat, tile_lon, col_offset, row_offset) tuples,
    where col_offset/row_offset are the tile's 0-indexed position in the canvas
    (top-left = 0,0).
    """
    ensure_dir(tile_dir)
    cx, cy = latlon_to_tile(centre_lat, centre_lon, zoom)
    half = grid // 2
    # Step 2 tile units between adjacent images: each 640px image is 2.5 tile
    # widths across, so step=2 gives a small overlap (~38m) with no black gaps.
    TILE_STEP = 2

    results = []
    for row in range(grid):
        for col in range(grid):
            tx = cx + (col - half) * TILE_STEP
            ty = cy + (row - half) * TILE_STEP
            tlat, tlon = tile_centre_latlon(tx, ty, zoom)
            fname = f"coord_{zoom}_{tx}_{ty}.png"
            fpath = tile_dir / fname

            if fpath.exists() and fpath.stat().st_size > 0:
                logger.debug("Tile cached: %s", fname)
            else:
                ok = _download_tile(tlat, tlon, zoom, fpath)
                if not ok:
                    logger.error("Failed to download tile (%d, %d)", tx, ty)
                    continue

            results.append((fpath, tlat, tlon, col, row))

    logger.info("Downloaded / cached %d tiles (%dx%d grid)", len(results), grid, grid)
    return results


# ── Image stitching ───────────────────────────────────────────────────────────


def stitch_tiles(
    tile_results: list[tuple[Path, float, float, int, int]],
    grid: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> np.ndarray:
    """
    Stitch downloaded tiles into a single large image.

    Tile positions (col, row) determine where each tile is placed in the canvas.
    Missing tiles are filled with black.
    """
    canvas_size = grid * tile_size
    canvas = np.zeros((canvas_size, canvas_size, 3), dtype=np.uint8)

    for fpath, _, _, col, row in tile_results:
        img = cv2.imread(str(fpath))
        if img is None:
            continue
        x0 = col * tile_size
        y0 = row * tile_size
        canvas[y0:y0 + tile_size, x0:x0 + tile_size] = img

    return canvas


# ── Grid-aware lat/lon -> pixel projection ────────────────────────────────────


_TILE_STEP = 2  # tile units between adjacent downloaded images (see download_grid)


def _metres_per_canvas_px(centre_lat: float, zoom: int, tile_size: int = DEFAULT_TILE_SIZE) -> float:
    """
    Return the real-world scale of one canvas pixel in metres.

    Adjacent tile centres are _TILE_STEP * 256 Web-Mercator pixels apart
    geographically, but placed tile_size canvas pixels apart in the stitch.
    Scale = (step * 256 WM px * metres_per_WM_px) / tile_size canvas px.
    """
    C = 40075016.686
    metres_per_wm_px = C * math.cos(math.radians(centre_lat)) / (2 ** (zoom + 8))
    return metres_per_wm_px * (_TILE_STEP * 256) / tile_size


def _latlon_to_grid_pixel(
    lat: float,
    lon: float,
    grid_centre_lat: float,
    grid_centre_lon: float,
    zoom: int,
    grid: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> tuple[int, int]:
    """
    Convert lat/lon to pixel coordinates on the stitched grid image.

    Uses the canvas scale (_metres_per_canvas_px) which accounts for the
    TILE_STEP=2 spacing between downloaded tile images.
    """
    mpp = _metres_per_canvas_px(grid_centre_lat, zoom, tile_size)

    dlat_m = (lat - grid_centre_lat) * (math.pi / 180) * 6371000
    dlon_m = (lon - grid_centre_lon) * (math.pi / 180) * 6371000 * math.cos(math.radians(grid_centre_lat))

    canvas_size = grid * tile_size
    cx, cy = canvas_size // 2, canvas_size // 2

    px = cx + int(dlon_m / mpp)
    py = cy - int(dlat_m / mpp)

    return max(0, min(canvas_size - 1, px)), max(0, min(canvas_size - 1, py))


# ── Annotation ────────────────────────────────────────────────────────────────


def annotate_image(
    img: np.ndarray,
    result: FootprintQueryResult,
    grid_centre_lat: float,
    grid_centre_lon: float,
    zoom: int,
    grid: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> np.ndarray:
    """
    Draw coloured building polygon overlays on the (stitched) satellite image.
    Each polygon label shows the building area in m2.
    """
    canvas_size = grid * tile_size
    overlay = img.copy()

    for i, bldg in enumerate(result.buildings):
        if not bldg.polygon_latlon or len(bldg.polygon_latlon) < 3:
            continue

        colour = _COLOURS[i % len(_COLOURS)]

        pts = np.array([
            _latlon_to_grid_pixel(lat, lon, grid_centre_lat, grid_centre_lon, zoom, grid, tile_size)
            for lon, lat in bldg.polygon_latlon
        ], dtype=np.int32)

        cv2.fillPoly(overlay, [pts], colour)
        cv2.polylines(img, [pts], isClosed=True, color=colour, thickness=2)

        cx = int(pts[:, 0].mean())
        cy = int(pts[:, 1].mean())
        label = f"{bldg.area_m2:.0f}m2"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.4
        thickness = 1
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        tx_label = max(0, min(canvas_size - tw - 4, cx - tw // 2))
        ty_label = max(th + 4, min(canvas_size - baseline - 2, cy))
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


def print_summary(result: FootprintQueryResult, tag: str, grid: int) -> str:
    """Format and print a summary table. Returns the text."""
    mpp = _metres_per_canvas_px(result.query_lat, DEFAULT_ZOOM)
    px_per_side = DEFAULT_TILE_SIZE * grid
    tile_area = (px_per_side * mpp) ** 2
    coverage_pct = (result.total_area_m2 / tile_area * 100) if tile_area > 0 else 0

    area_label = f"{grid}x{grid} tile grid" if grid > 1 else "single tile"
    side_m = int(px_per_side * mpp)

    lines = [
        "",
        "=" * 60,
        f"  Raising Rooves - Building Footprint Analysis: {tag}",
        "=" * 60,
        f"  Source           : OSM Overpass API (OpenStreetMap)",
        f"  Area queried     : {area_label} (~{side_m}x{side_m} m)",
        f"  Buildings found  : {result.count}",
        f"  Total roof area  : {result.total_area_m2:,.0f} m2",
        f"  Approx area      : {tile_area:,.0f} m2",
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
        "--grid",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Download an NxN grid of tiles and query all buildings in the combined area. "
            "1 = single tile (~190x190m), 3 = ~570x570m, 5 = ~950x950m. "
            "Must be an odd number. Default: 1."
        ),
    )
    parser.add_argument(
        "--footprint-file",
        type=Path,
        default=None,
        help="Optional: path to local GeoJSON footprint file (e.g. Microsoft AU Building Footprints). "
             "If omitted, queries OSM Overpass API.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.grid < 1 or args.grid % 2 == 0:
        parser.error("--grid must be an odd number >= 1 (e.g. 1, 3, 5, 7)")

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

    # Step 1: Download tile grid
    logger.info(
        "Downloading %dx%d tile grid around (%.5f, %.5f)...",
        args.grid, args.grid, lat, lon,
    )
    tile_results = download_grid(lat, lon, args.zoom, args.grid, tile_dir)
    if not tile_results:
        logger.error("No tiles downloaded. Check GOOGLE_MAPS_API_KEY.")
        sys.exit(1)

    # Compute the grid centre (centre tile's centre lat/lon)
    cx, cy = latlon_to_tile(lat, lon, args.zoom)
    grid_centre_lat, grid_centre_lon = tile_centre_latlon(cx, cy, args.zoom)

    # Compute bbox to match the full canvas extent.
    # Canvas is (grid * tile_size) px; each canvas pixel = _metres_per_canvas_px metres.
    # The OSM query must cover everything the canvas can show.
    mpp = _metres_per_canvas_px(grid_centre_lat, args.zoom)
    canvas_half_px = (args.grid * DEFAULT_TILE_SIZE) / 2
    metres_half = canvas_half_px * mpp * 1.05  # 5% padding so edge buildings aren't clipped

    dlat = metres_half / 111320.0
    dlon = metres_half / (111320.0 * math.cos(math.radians(grid_centre_lat)))

    south = grid_centre_lat - dlat
    north = grid_centre_lat + dlat
    west  = grid_centre_lon - dlon
    east  = grid_centre_lon + dlon

    # Step 2: Query building footprints for the full grid bbox
    source_label = f"local file: {args.footprint_file}" if args.footprint_file else "OSM Overpass API"
    logger.info("Querying building footprints via %s...", source_label)
    try:
        from stage1_segmentation.building_footprint_segmenter import BuildingFootprint
        buildings = query_buildings_in_bbox(
            south=south, west=west, north=north, east=east,
            local_file=args.footprint_file,
        )
    except (RuntimeError, FileNotFoundError) as exc:
        logger.error("%s", exc)
        sys.exit(1)

    footprint_result = FootprintQueryResult(
        query_lat=lat,
        query_lon=lon,
        tile_bbox=(south, west, north, east),
        buildings=buildings,
    )

    # Step 3: Print summary
    summary_text = print_summary(footprint_result, tag, args.grid)

    # Step 4: Stitch tiles and annotate
    stitched = stitch_tiles(tile_results, args.grid)
    annotated = annotate_image(
        stitched, footprint_result,
        grid_centre_lat, grid_centre_lon,
        args.zoom, args.grid,
    )

    img_path = out_dir / f"{tag}_annotated.png"
    cv2.imwrite(str(img_path), annotated)
    logger.info("Annotated image -> %s", img_path)

    # Step 5: Save CSV
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
