"""
Stage 1 pipeline orchestrator for the Raising Rooves pipeline.

Chains together: tile download → segmentation → classification → area estimation.
Checkpoint-aware: skips steps that have already been completed.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
from tqdm import tqdm

from config.settings import DEFAULT_ZOOM, MASKS_DIR, OUTPUT_DIR, TILES_DIR
from config.suburbs import Suburb, get_suburb
from shared.file_io import ensure_dir, save_parquet
from shared.logging_config import setup_logging
from stage1_segmentation.area_estimator import (
    RoofArea,
    aggregate_suburb_areas,
    estimate_tile_roofs,
    roof_areas_to_dataframe,
)
from stage1_segmentation.roof_classifier import classify_roof
from stage1_segmentation.sam_segmenter import load_segmentation_result
from stage1_segmentation.tile_downloader import download_tiles

logger = setup_logging("stage1_pipeline")


def _parse_tile_coords_from_filename(filename: str) -> tuple[int, int, int] | None:
    """
    Extract zoom, x, y from a tile filename like 'richmond_19_123456_234567.png'.

    Returns (zoom, x, y) or None if parsing fails.
    """
    parts = filename.replace(".png", "").split("_")
    try:
        # filename format: {suburb}_{zoom}_{x}_{y}
        zoom = int(parts[-3])
        x = int(parts[-2])
        y = int(parts[-1])
        return zoom, x, y
    except (ValueError, IndexError):
        return None


def _find_mask_for_tile(tile_path: Path, suburb_key: str) -> tuple[Path, Path] | None:
    """
    Look for a segmentation mask and metadata JSON for a given tile.

    Masks should be in data/processed/masks/{suburb}/ with names
    matching {tile_stem}_mask.png and {tile_stem}_meta.json.
    """
    mask_dir = MASKS_DIR / suburb_key
    stem = tile_path.stem

    mask_path = mask_dir / f"{stem}_mask.png"
    json_path = mask_dir / f"{stem}_meta.json"

    if mask_path.exists() and json_path.exists():
        return mask_path, json_path
    return None


def run_stage1(
    suburb_name: str,
    zoom: int = DEFAULT_ZOOM,
    skip_download: bool = False,
) -> pd.DataFrame:
    """
    Run the full Stage 1 pipeline for a single suburb.

    Steps:
        1. Look up suburb config (bbox, centroid)
        2. Download satellite tiles (or skip if present)
        3. Load segmentation masks (generated on Colab)
        4. Classify each roof by material/colour
        5. Estimate area in m²
        6. Aggregate and save results

    Args:
        suburb_name: Name of the suburb to process (must be in config/suburbs.py).
        zoom: Zoom level for tile download (default 19).
        skip_download: If True, skip tile download and use existing tiles.

    Returns:
        DataFrame with columns: suburb, roof_id, area_m2, pixel_count,
        material, colour, confidence, lat, lon.
    """
    suburb = get_suburb(suburb_name)
    suburb_key = suburb.name.lower().replace(" ", "_")

    logger.info("=" * 60)
    logger.info("Stage 1 Pipeline: %s (zoom=%d)", suburb.name, zoom)
    logger.info("=" * 60)

    # ── Step 1: Download tiles ───────────────────────────────────────────
    if skip_download:
        tile_dir = TILES_DIR / suburb_key
        tile_paths = sorted(tile_dir.glob("*.png")) if tile_dir.exists() else []
        logger.info("Skipping download. Found %d existing tiles.", len(tile_paths))
    else:
        logger.info("Step 1/4: Downloading satellite tiles...")
        tile_paths = download_tiles(suburb.name, suburb.bbox, zoom)

    if not tile_paths:
        logger.warning("No tiles found for '%s'. Cannot proceed.", suburb.name)
        return pd.DataFrame()

    # ── Step 2: Load segmentation masks ──────────────────────────────────
    logger.info("Step 2/4: Loading segmentation masks...")
    tiles_with_masks = 0
    tiles_without_masks = 0
    all_roof_areas: list[RoofArea] = []

    for tile_path in tqdm(tile_paths, desc="Processing tiles"):
        mask_result = _find_mask_for_tile(tile_path, suburb_key)

        if mask_result is None:
            tiles_without_masks += 1
            continue

        tiles_with_masks += 1
        mask_path, json_path = mask_result

        # Parse tile coordinates from filename
        coords = _parse_tile_coords_from_filename(tile_path.name)
        if coords is None:
            logger.warning("Cannot parse coords from: %s", tile_path.name)
            continue
        tile_zoom, tile_x, tile_y = coords

        # Load segmentation result
        seg_result = load_segmentation_result(mask_path, json_path)

        # ── Step 3: Classify each segment ────────────────────────────────
        tile_image = np.array(Image.open(tile_path).convert("RGB"))
        metadata = json.loads(json_path.read_text())

        segments_for_area = []
        for seg_meta in metadata["segments"]:
            # Create individual segment mask from the combined mask
            # For MVP, use the combined mask and classify based on pixel region
            seg_mask = seg_result.mask  # simplified: use combined mask

            classification = classify_roof(
                tile_image, seg_mask, segment_id=seg_meta["id"]
            )

            segments_for_area.append(
                {
                    "segment_id": seg_meta["id"],
                    "pixel_count": seg_meta["pixel_count"],
                    "centroid": seg_meta["centroid"],
                    "material": classification.material.value,
                    "colour": classification.colour.value,
                    "confidence": classification.confidence,
                }
            )

        # ── Step 4: Estimate areas ───────────────────────────────────────
        tile_roofs = estimate_tile_roofs(
            segments=segments_for_area,
            tile_x=tile_x,
            tile_y=tile_y,
            zoom=tile_zoom,
            suburb_name=suburb.name,
        )
        all_roof_areas.extend(tile_roofs)

    # ── Summary ──────────────────────────────────────────────────────────
    if tiles_without_masks > 0:
        logger.warning(
            "%d/%d tiles have no segmentation masks. "
            "Run SAM3 inference on Colab first (see notebooks/colab_sam3_inference.ipynb).",
            tiles_without_masks,
            len(tile_paths),
        )

    logger.info(
        "Processed %d tiles with masks, %d total roof segments.",
        tiles_with_masks,
        len(all_roof_areas),
    )

    # Aggregate
    summary = aggregate_suburb_areas(all_roof_areas, suburb.name)

    # Save results
    df = roof_areas_to_dataframe(all_roof_areas, suburb.name)
    if not df.empty:
        output_path = ensure_dir(OUTPUT_DIR) / f"stage1_{suburb_key}.parquet"
        save_parquet(df, output_path)
        logger.info("Results saved to: %s", output_path)

    logger.info("Stage 1 complete for %s.", suburb.name)
    return df
