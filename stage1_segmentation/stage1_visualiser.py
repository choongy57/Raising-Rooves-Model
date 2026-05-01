"""
Stage 1 visualiser — draws building footprint overlays on stitched satellite tiles.

Produces one annotated PNG per suburb saved alongside the Parquet output.
Uses the same tile images already downloaded by the pipeline (no extra downloads).
"""

import math
from pathlib import Path

import cv2
import numpy as np

from config.settings import DEFAULT_TILE_SIZE, DEFAULT_ZOOM, OUTPUT_DIR, TILES_DIR
from shared.logging_config import setup_logging
from stage1_segmentation.building_footprint_segmenter import BuildingFootprint

logger = setup_logging("stage1_visualiser")

_TILE_SIZE = DEFAULT_TILE_SIZE  # pixels per tile — matches tile_downloader

_COLOURS = [
    (255,  80,  80),   # red
    ( 80, 200,  80),   # green
    ( 80, 130, 255),   # blue
    (255, 200,  50),   # yellow
    (255, 130,  50),   # orange
    (180,  80, 255),   # purple
    ( 50, 220, 220),   # cyan
    (255, 100, 180),   # pink
]


# ── Coordinate helpers ────────────────────────────────────────────────────────


def _metres_per_px(centre_lat: float, zoom: int) -> float:
    """Metres represented by one pixel on the stitched canvas."""
    C = 40075016.686
    return C * math.cos(math.radians(centre_lat)) / (2 ** (zoom + 8))


def _latlon_to_canvas_px(
    lat: float,
    lon: float,
    canvas_centre_lat: float,
    canvas_centre_lon: float,
    zoom: int,
    canvas_w: int,
    canvas_h: int,
) -> tuple[int, int]:
    """Convert a lat/lon to pixel (x, y) on a stitched canvas image."""
    mpp = _metres_per_px(canvas_centre_lat, zoom)
    dlat_m = (lat - canvas_centre_lat) * (math.pi / 180) * 6371000
    dlon_m = (lon - canvas_centre_lon) * (math.pi / 180) * 6371000 * math.cos(
        math.radians(canvas_centre_lat)
    )
    cx = canvas_w // 2 + int(dlon_m / mpp)
    cy = canvas_h // 2 - int(dlat_m / mpp)
    return cx, cy


# ── Tile loading & stitching ──────────────────────────────────────────────────


def _tile_centre_latlon(x: int, y: int, zoom: int) -> tuple[float, float]:
    """Return the lat/lon centre of a web-mercator tile."""
    n = 2 ** zoom
    lon = (x + 0.5) / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 0.5) / n)))
    return math.degrees(lat_rad), lon


def _stitch_tiles(suburb_key: str, zoom: int) -> tuple[np.ndarray | None, float, float, int, int]:
    """
    Load all downloaded tiles and stitch them into a single georeferenced canvas.

    Each tile image is placed at its true geographic position rather than on a
    naive grid — necessary because 640px tiles cover 2.5 standard tile widths
    and therefore overlap each other significantly.

    Returns (canvas, centre_lat, centre_lon, canvas_w, canvas_h).
    Returns (None, 0, 0, 0, 0) if no tiles found.
    """
    tile_dir = TILES_DIR / suburb_key
    tile_paths = sorted(tile_dir.glob(f"{suburb_key}_{zoom}_*.png")) if tile_dir.exists() else []
    if not tile_paths:
        logger.warning("No tiles found in %s — skipping visualisation.", tile_dir)
        return None, 0.0, 0.0, 0, 0

    # Parse tile coords from filename: {suburb}_{zoom}_{x}_{y}.png
    tiles: list[tuple[int, int, Path]] = []
    for p in tile_paths:
        parts = p.stem.split("_")
        try:
            x, y = int(parts[-2]), int(parts[-1])
            tiles.append((x, y, p))
        except (IndexError, ValueError):
            continue

    if not tiles:
        return None, 0.0, 0.0, 0, 0

    # Canvas centre = mean of all tile centres in lat/lon
    centres = [_tile_centre_latlon(x, y, zoom) for x, y, _ in tiles]
    centre_lat = sum(c[0] for c in centres) / len(centres)
    centre_lon = sum(c[1] for c in centres) / len(centres)

    # Metres per pixel on the canvas (constant across the suburb at this zoom)
    C = 40075016.686
    mpp = C * math.cos(math.radians(centre_lat)) / (2 ** (zoom + 8))

    # Each tile image covers _TILE_SIZE pixels → half-width in metres
    half_tile_m = (_TILE_SIZE / 2) * mpp

    # Determine canvas extent from the outermost tile edges
    all_lats = [c[0] for c in centres]
    all_lons = [c[1] for c in centres]
    bbox_lat = max(all_lats) - min(all_lats)
    bbox_lon = max(all_lons) - min(all_lons)
    margin_m = half_tile_m * 1.1
    canvas_h = int((bbox_lat * 111320 + 2 * margin_m) / mpp)
    canvas_w = int((bbox_lon * 111320 * math.cos(math.radians(centre_lat)) + 2 * margin_m) / mpp)
    # Ensure even dimensions
    canvas_h = max(canvas_h, _TILE_SIZE)
    canvas_w = max(canvas_w, _TILE_SIZE)

    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

    placed = 0
    for (tx, ty, p), (tlat, tlon) in zip(tiles, centres):
        img = cv2.imread(str(p))
        if img is None:
            continue
        ih, iw = img.shape[:2]

        # Centre of this tile on the canvas in pixels
        dlat_m = (tlat - centre_lat) * (math.pi / 180) * 6371000
        dlon_m = (tlon - centre_lon) * (math.pi / 180) * 6371000 * math.cos(math.radians(centre_lat))
        cx = canvas_w // 2 + int(dlon_m / mpp)
        cy = canvas_h // 2 - int(dlat_m / mpp)

        # Blit tile onto canvas, clipping to canvas bounds
        x0 = cx - iw // 2
        y0 = cy - ih // 2
        x1, y1 = x0 + iw, y0 + ih

        # Source crop (in case tile hangs off canvas edge)
        sx0 = max(0, -x0);  sx1 = iw - max(0, x1 - canvas_w)
        sy0 = max(0, -y0);  sy1 = ih - max(0, y1 - canvas_h)
        dx0 = max(0, x0);   dx1 = min(canvas_w, x1)
        dy0 = max(0, y0);   dy1 = min(canvas_h, y1)

        if dx1 > dx0 and dy1 > dy0:
            canvas[dy0:dy1, dx0:dx1] = img[sy0:sy1, sx0:sx1]
            placed += 1

    logger.info(
        "Stitched %d/%d tiles into %dx%d canvas (centre %.5f, %.5f)",
        placed, len(tiles), canvas_w, canvas_h, centre_lat, centre_lon,
    )
    return canvas, centre_lat, centre_lon, canvas_w, canvas_h


# ── Annotation ────────────────────────────────────────────────────────────────


def _annotate(
    canvas: np.ndarray,
    buildings: list[BuildingFootprint],
    centre_lat: float,
    centre_lon: float,
    zoom: int,
    canvas_w: int,
    canvas_h: int,
) -> np.ndarray:
    """Draw coloured polygon overlays with area labels onto the canvas."""
    overlay = canvas.copy()

    n_rendered = 0
    n_off_canvas = 0

    for i, bldg in enumerate(buildings):
        if not bldg.polygon_latlon or len(bldg.polygon_latlon) < 3:
            continue

        colour = _COLOURS[i % len(_COLOURS)]
        pts = np.array([
            _latlon_to_canvas_px(lat, lon, centre_lat, centre_lon, zoom, canvas_w, canvas_h)
            for lon, lat in bldg.polygon_latlon
        ], dtype=np.int32)

        # Skip buildings whose projected bounding box lies entirely outside the canvas.
        # cv2.fillPoly clips correctly, but a fully off-canvas building wastes work
        # and its label would be placed at the canvas edge, not on the building.
        xs, ys = pts[:, 0], pts[:, 1]
        if xs.max() < 0 or xs.min() >= canvas_w or ys.max() < 0 or ys.min() >= canvas_h:
            n_off_canvas += 1
            logger.debug(
                "Building %s: polygon projects entirely outside canvas (%dx%d) — skipping annotation",
                bldg.building_id, canvas_w, canvas_h,
            )
            continue

        cv2.fillPoly(overlay, [pts], colour)
        cv2.polylines(canvas, [pts], isClosed=True, color=colour, thickness=2)

        cx = int(pts[:, 0].mean())
        cy = int(pts[:, 1].mean())
        label = f"{bldg.area_m2:.0f}m²"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale, thick = 0.35, 1
        (tw, th), baseline = cv2.getTextSize(label, font, scale, thick)
        tx = max(0, min(canvas_w - tw - 4, cx - tw // 2))
        ty = max(th + 4, min(canvas_h - baseline - 2, cy))
        cv2.rectangle(canvas, (tx - 2, ty - th - 2), (tx + tw + 2, ty + baseline), (0, 0, 0), -1)
        cv2.putText(canvas, label, (tx, ty), font, scale, colour, thick, cv2.LINE_AA)
        n_rendered += 1

    if n_off_canvas:
        logger.info(
            "Annotation: %d buildings rendered, %d skipped (projected entirely off canvas)",
            n_rendered, n_off_canvas,
        )
    else:
        logger.info("Annotation: %d buildings rendered.", n_rendered)

    cv2.addWeighted(overlay, 0.25, canvas, 0.75, 0, canvas)
    return canvas


# ── Public entry point ────────────────────────────────────────────────────────


def save_visualisation(
    suburb_name: str,
    buildings: list[BuildingFootprint],
    zoom: int = DEFAULT_ZOOM,
) -> Path | None:
    """
    Stitch downloaded tiles for the suburb and draw building footprint overlays.

    Saves the annotated PNG to data/output/stage1_{suburb_key}_annotated.png.
    Returns the output path, or None if no tiles were available.

    Args:
        suburb_name: Suburb name (used to locate tiles in TILES_DIR).
        buildings: BuildingFootprint list from the segmentation step.
        zoom: Zoom level used when tiles were downloaded.
    """
    from config.suburbs import get_suburb
    suburb_key = get_suburb(suburb_name).key
    canvas, centre_lat, centre_lon, canvas_w, canvas_h = _stitch_tiles(suburb_key, zoom)
    if canvas is None:
        return None

    logger.info("Annotating %d buildings on canvas...", len(buildings))
    annotated = _annotate(canvas, buildings, centre_lat, centre_lon, zoom, canvas_w, canvas_h)

    out_path = OUTPUT_DIR / f"stage1_{suburb_key}_annotated.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), annotated)
    logger.info("Annotated image saved to: %s", out_path)
    return out_path
