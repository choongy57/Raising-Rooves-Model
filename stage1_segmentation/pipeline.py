"""
Stage 1 pipeline orchestrator for the Raising Rooves pipeline.

Chains together: tile download -> OSM building footprint query -> area aggregation.

Data source: OpenStreetMap Overpass API (no GPU, no API key, no large download).
One Overpass query covers the entire suburb bbox, so suburb-scale runs
issue a single HTTP request rather than one per tile.

Optional local source: Microsoft Australia Building Footprints GeoJSON
(pass footprint_file= to run_stage1).
"""

from pathlib import Path

import pandas as pd
from tqdm import tqdm

from config.settings import DEFAULT_ZOOM, OUTPUT_DIR, TILES_DIR
from config.suburbs import get_suburb
from shared.file_io import ensure_dir, save_parquet
from shared.geo_utils import compute_tile_grid, latlon_to_tile, tile_centre_latlon
from shared.logging_config import setup_logging
from stage1_segmentation.building_footprint_segmenter import (
    BuildingFootprint,
    merge_footprints,
    query_buildings_in_bbox,
)
from stage1_segmentation.stage1_visualiser import save_visualisation
from stage1_segmentation.tile_downloader import download_tiles

logger = setup_logging("stage1_pipeline")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _building_to_row(
    building: BuildingFootprint,
    suburb_name: str,
    idx: int,
) -> dict:
    """Convert a BuildingFootprint to a DataFrame-ready dict."""
    lons = [c[0] for c in building.polygon_latlon]
    lats = [c[1] for c in building.polygon_latlon]
    centroid_lat = sum(lats) / len(lats) if lats else 0.0
    centroid_lon = sum(lons) / len(lons) if lons else 0.0

    return {
        "suburb": suburb_name,
        "building_id": building.building_id,
        "roof_id": f"{suburb_name.lower().replace(' ', '_')}_{building.building_id}",
        "area_m2": building.area_m2,
        "lat": round(centroid_lat, 6),
        "lon": round(centroid_lon, 6),
        "source": building.source,
        "building_type": building.building_type,
        "levels": building.levels,
        "roof_material": building.roof_material,
        "roof_colour": building.roof_colour,
        "roof_shape": building.roof_shape,
    }


# ── Main pipeline ─────────────────────────────────────────────────────────────


def run_stage1(
    suburb_name: str,
    zoom: int = DEFAULT_ZOOM,
    skip_download: bool = False,
    max_tiles: int | None = None,
    footprint_file: Path | None = None,
    merge_footprint_file: Path | None = None,
) -> pd.DataFrame:
    """
    Run the full Stage 1 pipeline for a single suburb.

    Steps:
        1. Look up suburb config (bbox)
        2. Download satellite tiles for visual reference (or skip)
        3. Query building footprints (OSM and/or local file)
        4. Aggregate and save results to Parquet
        5. Generate annotated visualisation PNG

    Args:
        suburb_name: Suburb to process (must be in config/suburbs.py).
        zoom: Zoom level for tile download (default 19).
        skip_download: Skip tile download (tiles only used as visual reference).
        max_tiles: Cap tiles downloaded (smoke-test mode).
        footprint_file: Use ONLY this local file (SHP/GeoJSON) — skips OSM.
        merge_footprint_file: Merge this local file WITH OSM. OSM buildings are
            kept as primary; local buildings not overlapping OSM are added.

    Returns:
        DataFrame with columns: suburb, building_id, roof_id, area_m2, lat, lon, source.
    """
    suburb = get_suburb(suburb_name)
    suburb_key = suburb.name.lower().replace(" ", "_")
    south, west, north, east = suburb.bbox

    logger.info("=" * 60)
    logger.info("Stage 1 Pipeline: %s (zoom=%d)", suburb.name, zoom)
    logger.info("Suburb bbox: (%.5f, %.5f) -> (%.5f, %.5f)", south, west, north, east)
    logger.info("=" * 60)

    # ── Step 1: Download satellite tiles (visual reference) ───────────────
    if skip_download:
        logger.info("Step 1/4: Skipping tile download (--skip-download).")
    else:
        logger.info("Step 1/4: Downloading satellite tiles...")
        tile_paths = download_tiles(suburb.name, suburb.bbox, zoom)
        if max_tiles and len(tile_paths) > max_tiles:
            logger.info("Smoke-test: capping at %d/%d tiles.", max_tiles, len(tile_paths))
            tile_paths = tile_paths[:max_tiles]
        logger.info("Downloaded %d tiles.", len(tile_paths))

    # ── Step 2: Query building footprints for the whole suburb ────────────
    if merge_footprint_file:
        source_label = f"OSM + {merge_footprint_file.name}"
    elif footprint_file:
        source_label = f"local file: {footprint_file.name}"
    else:
        source_label = "OSM Overpass API"
    logger.info("Step 2/4: Querying building footprints via %s...", source_label)

    try:
        buildings = query_buildings_in_bbox(
            south=south, west=west, north=north, east=east,
            local_file=footprint_file,
        )
        if merge_footprint_file:
            logger.info("Merging with local file: %s...", merge_footprint_file.name)
            secondary = query_buildings_in_bbox(
                south=south, west=west, north=north, east=east,
                local_file=merge_footprint_file,
            )
            buildings = merge_footprints(buildings, secondary)
    except (RuntimeError, FileNotFoundError) as exc:
        logger.error("Footprint query failed: %s", exc)
        return pd.DataFrame()

    if not buildings:
        logger.warning("No buildings found in %s. Check suburb bbox.", suburb_name)
        return pd.DataFrame()

    logger.info("Found %d buildings in %s.", len(buildings), suburb_name)

    # ── Step 3: Build DataFrame and save ─────────────────────────────────
    logger.info("Step 3/4: Aggregating results...")

    rows = []
    for i, bldg in enumerate(tqdm(buildings, desc="Processing buildings")):
        rows.append(_building_to_row(bldg, suburb.name, i))

    df = pd.DataFrame(rows)

    # Summary stats
    total_area = df["area_m2"].sum()
    mean_area = df["area_m2"].mean()
    logger.info(
        "Suburb %s: %d buildings | total %.0f m2 | mean %.0f m2/building",
        suburb.name, len(df), total_area, mean_area,
    )

    # Save
    output_path = ensure_dir(OUTPUT_DIR) / f"stage1_{suburb_key}.parquet"
    save_parquet(df, output_path)
    logger.info("Results saved to: %s", output_path)

    # ── Step 4: Visualise ─────────────────────────────────────────────────
    if not skip_download:
        logger.info("Step 4/4: Generating annotated visualisation...")
        img_path = save_visualisation(suburb.name, buildings, zoom)
        if img_path:
            logger.info("Annotated image: %s", img_path)

    logger.info("=" * 60)
    logger.info("Stage 1 complete for %s.", suburb.name)
    logger.info("=" * 60)

    return df
