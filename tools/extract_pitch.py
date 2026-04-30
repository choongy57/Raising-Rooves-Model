"""
Roof Pitch Extractor — Raising Rooves pipeline tool

Reads a Stage 1 parquet (building footprints with polygon_latlon) and a
GeoTIFF DSM, then extracts a pitch angle for each building footprint using
RANSAC + SVD plane fitting on the DSM elevation points.

## Quick start

1. Download a DSM for your suburb (see sources below).
2. Run Stage 1 for your suburb to produce the parquet:
       python -m stage1_segmentation.run_stage1 --suburb Clayton
3. Run this tool:
       python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif

## DSM sources (free)

  High-res (STRONGLY recommended, 1 m):
    ELVIS — https://elevation.fsdf.org.au/
      1. Register (free) and log in.
      2. Click "Order Data" and draw your suburb bounding box on the map.
      3. Select "Digital Elevation Model (DEM) 1m" as the product type.
      4. Fill in your contact details and submit.
      5. You will receive an email with a download link (valid 48 hours).
      6. Download and unzip the GeoTIFF, then run:
           python -m tools.extract_pitch --suburb Clayton --import-dsm /path/to/download.tif
         OR copy the file manually and use:
           python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif

      NOTE: ELVIS does not currently offer a public programmatic download API.
      The web portal is the only supported access method as of 2026.

  Inner Melbourne only (1 m DSM, no registration):
    City of Melbourne Open Data — https://data.melbourne.vic.gov.au/
      Search "DSM"; direct download GeoTIFF.
      Use --import-dsm or --dsm-file with the downloaded file.

  Programmatic fallback (30 m, any suburb):
    OpenTopography COP30 — set OPENTOPO_API_KEY in .env, then:
       python -m tools.extract_pitch --suburb Clayton --download-cop30

    WARNING: COP30 is ~30 m resolution. Pitch estimates from this source
    are unreliable for individual residential buildings. Use ELVIS 1 m data
    for defensible results.

## Importing a manually downloaded DSM

Use --import-dsm to validate and register a DSM file you have downloaded:

    python -m tools.extract_pitch --suburb Clayton --import-dsm /path/to/dem_1m.tif

This copies the file to data/raw/dsm/<suburb_key>.tif after checking:
  - The file is a valid GeoTIFF readable by rasterio
  - The file has a recognised CRS
  - The pixel resolution is reported in the log
  - The bounding box overlaps the suburb area of interest

After importing, use --dsm-file data/raw/dsm/<suburb_key>.tif for subsequent runs.

Outputs (saved to data/output/):
    stage1_<suburb>_with_pitch.parquet  — enhanced parquet, adds columns:
        pitch_deg        float  roof pitch (°), NaN if extraction failed
        aspect_deg       float  downhill direction (° CW from North), NaN if flat/failed
        pitch_plane_rmse float  plane fit residual (m), NaN if failed
        pitch_n_points   int    DSM points in footprint before outlier removal
        pitch_n_inliers  int    points used for final fit
        pitch_flag       str    "ok" | "flat" | "unrealistic" | "too_few_points" |
                                "ransac_failed" | "extraction_failed"
    stage1_<suburb>_with_pitch.csv      — same as CSV
    stage1_<suburb>_pitch_map.png       — map: buildings coloured by pitch angle

Usage:
    python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif
    python -m tools.extract_pitch --suburb Richmond --dsm-file data/raw/dsm/richmond.tif --debug
    python -m tools.extract_pitch --suburb Clayton --download-cop30
    python -m tools.extract_pitch --suburb Clayton --import-dsm /path/to/elvis_dem.tif
    python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif --buffer 1.0
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Project root on sys.path ──────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import OUTPUT_DIR, RAW_DIR
from shared.file_io import ensure_dir, load_parquet, save_parquet
from shared.logging_config import setup_logging

logger = setup_logging("extract_pitch")

DSM_DIR = RAW_DIR / "dsm"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _suburb_key(suburb_name: str) -> str:
    return suburb_name.lower().replace(" ", "_")


def _load_stage1_parquet(suburb_name: str) -> pd.DataFrame:
    """Load Stage 1 output parquet for a suburb."""
    key = _suburb_key(suburb_name)
    path = OUTPUT_DIR / f"stage1_{key}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Stage 1 parquet not found: {path}\n"
            f"Run:  python -m stage1_segmentation.run_stage1 --suburb \"{suburb_name}\""
        )
    df = load_parquet(path)
    logger.info("Loaded Stage 1 data: %d buildings from %s", len(df), path.name)
    return df


def _load_polygon_latlons(suburb_name: str) -> list[list[list[float]]]:
    """
    Load per-building polygon_latlon lists from Stage 1 parquet.

    Stage 1 stores polygon_latlon as a JSON string column (added by pipeline).
    If the column is missing, returns empty lists for every row.
    """
    key = _suburb_key(suburb_name)
    # The pipeline saves the polygon separately from the main parquet
    # because polygon data can be large. It's in a _polygons.json sidecar.
    sidecar = OUTPUT_DIR / f"stage1_{key}_polygons.json"
    if sidecar.exists():
        with open(sidecar) as fh:
            return json.load(fh)
    return []


def _render_pitch_map(
    df: pd.DataFrame,
    output_path: Path,
) -> None:
    """
    Render a static map coloured by pitch angle using matplotlib.

    Buildings with valid pitch are coloured on a blue→yellow→red scale.
    Failed / flat buildings are shown in light grey.
    """
    try:
        import matplotlib.pyplot as plt
        import matplotlib.cm as cm
        import matplotlib.colors as mcolors
        from matplotlib.patches import Polygon as MplPolygon
        from matplotlib.collections import PatchCollection
        import json
    except ImportError:
        logger.warning("matplotlib not available — skipping pitch map render.")
        return

    fig, ax = plt.subplots(figsize=(12, 10))

    valid = df[df["pitch_deg"].notna() & (df["pitch_flag"] != "unrealistic")]
    invalid = df[~df.index.isin(valid.index)]

    # Colour scale: 0° (blue) → 22.5° (green) → 45°+ (red)
    cmap = cm.get_cmap("RdYlBu_r")
    norm = mcolors.Normalize(vmin=0, vmax=45)

    # Plot invalid/flat buildings in grey
    if len(invalid) > 0:
        ax.scatter(
            invalid["lon"], invalid["lat"],
            c="lightgrey", s=6, alpha=0.5, linewidths=0, zorder=1,
            label="No pitch data",
        )

    # Plot valid buildings coloured by pitch
    if len(valid) > 0:
        sc = ax.scatter(
            valid["lon"], valid["lat"],
            c=valid["pitch_deg"], cmap=cmap, norm=norm,
            s=10, alpha=0.8, linewidths=0, zorder=2,
        )
        cbar = plt.colorbar(sc, ax=ax, fraction=0.03, pad=0.01)
        cbar.set_label("Roof pitch (°)", fontsize=11)
        cbar.set_ticks([0, 10, 20, 30, 45])

    suburb = df["suburb"].iloc[0] if "suburb" in df.columns else "Unknown"
    n_ok = (df["pitch_flag"] == "ok").sum()
    n_flat = (df["pitch_flag"] == "flat").sum()
    n_failed = df["pitch_flag"].isin(["too_few_points", "ransac_failed", "extraction_failed"]).sum()

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(
        f"Roof Pitch Map — {suburb}\n"
        f"{n_ok} pitched  |  {n_flat} flat  |  {n_failed} failed",
        fontsize=13,
    )
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3, linewidth=0.5)

    plt.tight_layout()
    plt.savefig(str(output_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Pitch map saved: %s", output_path)


def _print_summary(df: pd.DataFrame, suburb_name: str) -> None:
    """Print pitch angle statistics to the logger."""
    valid = df[df["pitch_deg"].notna()]
    n_total = len(df)
    n_valid = len(valid)

    logger.info("=" * 55)
    logger.info("Pitch summary — %s", suburb_name)
    logger.info("  Total buildings : %d", n_total)
    logger.info("  With pitch data : %d (%.0f%%)", n_valid, 100 * n_valid / max(n_total, 1))

    if n_valid > 0:
        p = valid["pitch_deg"]
        logger.info("  Pitch (degrees) : min=%.1f  median=%.1f  mean=%.1f  max=%.1f",
                    p.min(), p.median(), p.mean(), p.max())

    flag_counts = df["pitch_flag"].value_counts()
    logger.info("  Flag breakdown  :")
    for flag, count in flag_counts.items():
        logger.info("    %-22s %d", flag, count)
    logger.info("=" * 55)


# ── DSM import helper ─────────────────────────────────────────────────────────


def _import_dsm(
    source_path: Path,
    suburb_name: str,
    suburb_bbox: tuple[float, float, float, float],
) -> Path:
    """
    Validate and copy a manually downloaded DSM GeoTIFF into data/raw/dsm/.

    Performs the following checks:
      1. Source file exists and is readable by rasterio.
      2. The file has a recognised CRS (warns if not).
      3. Logs pixel resolution so the user can verify 1 m vs coarser data.
      4. Warns if the DSM bounding box does not overlap the suburb bbox.

    The validated file is copied to:
        data/raw/dsm/<suburb_key>.tif

    Args:
        source_path:  Path to the user-supplied GeoTIFF.
        suburb_name:  Suburb name (used to derive the output filename).
        suburb_bbox:  Suburb bounding box (south, west, north, east) in
                      EPSG:4326, used to check spatial overlap.

    Returns:
        Path to the copied file under data/raw/dsm/.

    Raises:
        FileNotFoundError: If source_path does not exist.
        RuntimeError: If rasterio cannot open the file or a critical CRS
                      error prevents copying.
    """
    try:
        import rasterio
    except ImportError as exc:
        raise ImportError(
            "rasterio is required. Install with: pip install rasterio"
        ) from exc

    if not source_path.exists():
        raise FileNotFoundError(f"Source DSM not found: {source_path}")

    # ── Open and inspect ──────────────────────────────────────────────────
    try:
        with rasterio.open(str(source_path)) as ds:
            crs = ds.crs
            width = ds.width
            height = ds.height
            transform = ds.transform
            bounds = ds.bounds

            # CRS check
            if crs is None:
                logger.warning(
                    "DSM has no CRS metadata. Proceeding, but results may be "
                    "incorrect if the file is not in EPSG:4326 or a compatible "
                    "projected CRS. Verify with: gdalinfo %s",
                    source_path,
                )
                epsg_str = "unknown"
            else:
                epsg = crs.to_epsg()
                epsg_str = str(epsg) if epsg else f"(custom: {crs.to_string()[:60]})"
                logger.info("DSM CRS: EPSG:%s", epsg_str)

            # Pixel resolution
            px_w = abs(transform.a)
            px_h = abs(transform.e)
            logger.info(
                "DSM size: %d x %d pixels | pixel size: %.4f x %.4f (CRS units)",
                width, height, px_w, px_h,
            )
            # Heuristic: if CRS is geographic (degrees), 1 m ≈ 8.98e-6 deg
            if crs and crs.is_geographic:
                approx_res_m = px_w * 111320.0
                logger.info(
                    "Approximate resolution (geographic CRS): ~%.1f m/pixel", approx_res_m
                )
                if approx_res_m > 10.0:
                    logger.warning(
                        "DSM resolution appears coarser than 10 m (~%.0f m). "
                        "Pitch estimates for individual buildings will be unreliable. "
                        "For best results, use ELVIS 1 m data from "
                        "https://elevation.fsdf.org.au/",
                        approx_res_m,
                    )
            else:
                # Projected CRS: pixel size is in metres
                if px_w > 10.0:
                    logger.warning(
                        "DSM resolution appears coarser than 10 m (%.1f m). "
                        "Pitch estimates for individual buildings will be unreliable. "
                        "For best results, use ELVIS 1 m data from "
                        "https://elevation.fsdf.org.au/",
                        px_w,
                    )
                else:
                    logger.info("DSM resolution: %.1f m/pixel — suitable for building pitch extraction.", px_w)

            # Bounding-box overlap check
            # bounds is in the DSM's native CRS; convert to approx lat/lon for check
            # For a quick sanity check we use the raw bounds if geographic,
            # or log a best-effort note if projected.
            south, west, north, east = suburb_bbox
            if crs and crs.is_geographic:
                dsm_west, dsm_south, dsm_east, dsm_north = (
                    bounds.left, bounds.bottom, bounds.right, bounds.top
                )
                overlap = not (
                    dsm_east < west or dsm_west > east
                    or dsm_north < south or dsm_south > north
                )
                if not overlap:
                    logger.warning(
                        "DSM bounding box (%.4f, %.4f, %.4f, %.4f) does not appear to "
                        "overlap the suburb bbox (%.4f, %.4f, %.4f, %.4f). "
                        "Pitch extraction will produce empty results unless you have "
                        "the correct tile.",
                        dsm_south, dsm_west, dsm_north, dsm_east,
                        south, west, north, east,
                    )
                else:
                    logger.info("DSM bounding box overlaps suburb area of interest.")
            else:
                logger.info(
                    "DSM is in a projected CRS — skipping automatic overlap check. "
                    "Verify the tile covers your suburb before proceeding."
                )

    except Exception as exc:
        raise RuntimeError(
            f"Cannot open {source_path} as a GeoTIFF: {exc}"
        ) from exc

    # ── Copy to DSM_DIR ───────────────────────────────────────────────────
    key = _suburb_key(suburb_name)
    dest_path = DSM_DIR / f"{key}.tif"
    DSM_DIR.mkdir(parents=True, exist_ok=True)

    if dest_path.exists():
        logger.warning(
            "Overwriting existing DSM at %s", dest_path
        )

    shutil.copy2(str(source_path), str(dest_path))
    logger.info(
        "DSM imported successfully: %s -> %s",
        source_path.name, dest_path,
    )
    logger.info(
        "To run pitch extraction with this DSM:\n"
        "  python -m tools.extract_pitch --suburb \"%s\" --dsm-file %s",
        suburb_name, dest_path,
    )
    return dest_path


# ── Main ──────────────────────────────────────────────────────────────────────


def run_extract_pitch(
    suburb_name: str,
    dsm_path: Path | None,
    buffer_m: float = 0.5,
    download_cop30: bool = False,
    import_dsm_path: Path | None = None,
) -> pd.DataFrame:
    """
    Full pitch extraction pipeline for a suburb.

    Args:
        suburb_name:     Suburb to process (must have a Stage 1 parquet).
        dsm_path:        Path to GeoTIFF DSM. If None and download_cop30=True,
                         downloads COP30 automatically.
        buffer_m:        Outward buffer when clipping DSM to footprint (metres).
        download_cop30:  If True and dsm_path is None, download COP30 DSM first.
        import_dsm_path: If provided, validate and import this file into
                         data/raw/dsm/ before running extraction.

    Returns:
        Enhanced DataFrame with pitch columns added.
    """
    from stage1_segmentation.dsm_processor import DSM_DIR, download_cop30 as dl_cop30, load_dsm
    from stage1_segmentation.pitch_extractor import batch_extract_pitch
    from config.suburbs import get_suburb

    suburb = get_suburb(suburb_name)
    key = _suburb_key(suburb_name)

    # ── Step 0: Import DSM if requested ──────────────────────────────────
    if import_dsm_path is not None:
        dsm_path = _import_dsm(import_dsm_path, suburb_name, suburb.bbox)

    # ── Step 1: Resolve DSM path ──────────────────────────────────────────
    if dsm_path is None:
        if download_cop30:
            dsm_path = DSM_DIR / f"{key}_cop30.tif"
            if not dsm_path.exists():
                logger.info("Downloading COP30 DSM for %s...", suburb_name)
                dl_cop30(suburb.bbox, dsm_path)
            else:
                logger.info("Reusing existing COP30 DSM: %s", dsm_path.name)
            logger.warning(
                "COP30 is a ~30 m resolution global DEM. Pitch estimates derived "
                "from this source are unreliable for individual residential buildings "
                "(a typical Melbourne house is only 10–20 m wide). "
                "For defensible per-building pitch, download the free ELVIS 1 m DEM "
                "from https://elevation.fsdf.org.au/ and use --import-dsm <path>."
            )
        else:
            raise ValueError(
                "Provide --dsm-file, --import-dsm, or use --download-cop30 to fetch "
                "a 30 m fallback DSM.\n"
                "For best results, download the free ELVIS 1 m DEM from "
                "https://elevation.fsdf.org.au/ and use --import-dsm <path>."
            )

    # ── Step 2: Load DSM and Stage 1 parquet ──────────────────────────────
    dsm = load_dsm(dsm_path)
    df = _load_stage1_parquet(suburb_name)

    # ── Step 3: Load polygon geometry ────────────────────────────────────
    polygon_latlons = _load_polygon_latlons(suburb_name)
    if not polygon_latlons:
        logger.warning(
            "No polygon sidecar found for %s. "
            "Polygon data is saved by run_stage1 but may be absent for older runs. "
            "Re-run Stage 1 to regenerate it, or pitch_deg will be NaN for all buildings.",
            suburb_name,
        )
        # Fill with empty polygons — all results will be "extraction_failed"
        polygon_latlons = [[] for _ in range(len(df))]

    # Align polygon list length to DataFrame
    if len(polygon_latlons) != len(df):
        logger.warning(
            "Polygon sidecar has %d entries but parquet has %d rows. "
            "Padding with empty polygons for missing entries.",
            len(polygon_latlons), len(df),
        )
        while len(polygon_latlons) < len(df):
            polygon_latlons.append([])

    building_ids = df["building_id"].astype(str).tolist()

    # ── Step 4: Extract pitch ─────────────────────────────────────────────
    logger.info("Extracting pitch for %d buildings...", len(df))
    results = batch_extract_pitch(
        dsm=dsm,
        polygon_latlons=polygon_latlons,
        building_ids=building_ids,
        buffer_m=buffer_m,
    )

    # ── Step 5: Merge results into DataFrame ──────────────────────────────
    df["pitch_deg"] = [r.pitch_deg for r in results]
    df["aspect_deg"] = [r.aspect_deg for r in results]
    df["pitch_plane_rmse"] = [r.plane_rmse for r in results]
    df["pitch_n_points"] = [r.n_points for r in results]
    df["pitch_n_inliers"] = [r.n_inliers for r in results]
    df["pitch_flag"] = [r.flag for r in results]

    # ── Step 6: Save outputs ──────────────────────────────────────────────
    out_dir = ensure_dir(OUTPUT_DIR)
    parquet_out = out_dir / f"stage1_{key}_with_pitch.parquet"
    csv_out = out_dir / f"stage1_{key}_with_pitch.csv"
    map_out = out_dir / f"stage1_{key}_pitch_map.png"

    save_parquet(df, parquet_out)
    df.to_csv(csv_out, index=False)
    logger.info("Saved parquet: %s", parquet_out)
    logger.info("Saved CSV:     %s", csv_out)

    _render_pitch_map(df, map_out)
    _print_summary(df, suburb_name)

    dsm._dataset.close()
    return df


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract roof pitch angles from DSM for Stage 1 buildings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--suburb", required=True,
        help="Suburb name matching config/suburbs.py (e.g. 'Clayton', 'Richmond').",
    )
    parser.add_argument(
        "--dsm-file", type=Path, default=None,
        help="Path to a GeoTIFF DSM file. Skip if using --download-cop30.",
    )
    parser.add_argument(
        "--download-cop30", action="store_true",
        help=(
            "Download a 30 m Copernicus DEM (COP30) automatically via OpenTopography. "
            "Requires OPENTOPO_API_KEY in .env. "
            "WARNING: 30 m resolution is unreliable for individual building pitch — "
            "use ELVIS 1 m data (--import-dsm) for defensible results."
        ),
    )
    parser.add_argument(
        "--import-dsm", type=Path, default=None, metavar="PATH",
        dest="import_dsm",
        help=(
            "Path to a manually downloaded GeoTIFF DSM (e.g. from ELVIS or City of "
            "Melbourne Open Data). Validates the file and copies it to "
            "data/raw/dsm/<suburb_key>.tif, then runs pitch extraction. "
            "ELVIS 1 m data: https://elevation.fsdf.org.au/"
        ),
    )
    parser.add_argument(
        "--buffer", type=float, default=0.5, metavar="METRES",
        help="Outward footprint buffer when clipping DSM (default: 0.5 m).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable DEBUG-level logging.",
    )

    args = parser.parse_args()

    if args.debug:
        import logging
        logging.getLogger().setLevel(logging.DEBUG)

    if args.dsm_file is None and not args.download_cop30 and args.import_dsm is None:
        parser.error(
            "Provide one of:\n"
            "  --dsm-file <path>       use an existing DSM GeoTIFF\n"
            "  --import-dsm <path>     validate and import a downloaded DSM\n"
            "  --download-cop30        download 30 m COP30 fallback (unreliable for buildings)\n"
            "\n"
            "For best results, download the free ELVIS 1 m DEM from "
            "https://elevation.fsdf.org.au/ and use --import-dsm."
        )

    try:
        run_extract_pitch(
            suburb_name=args.suburb,
            dsm_path=args.dsm_file,
            buffer_m=args.buffer,
            download_cop30=args.download_cop30,
            import_dsm_path=args.import_dsm,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
