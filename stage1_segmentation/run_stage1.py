"""
CLI entry point for Stage 1: Roof Segmentation.

Usage:
    python -m stage1_segmentation.run_stage1 --suburb "Richmond"
    python -m stage1_segmentation.run_stage1 --suburb "Richmond" --debug
    python -m stage1_segmentation.run_stage1 --suburb "Richmond" --skip-download
    python -m stage1_segmentation.run_stage1 --suburb "Richmond" --max-tiles 10
    python -m stage1_segmentation.run_stage1 --suburb "Richmond" --footprint-file data/raw/footprints/australia.geojson
    python -m stage1_segmentation.run_stage1 --list-suburbs
"""

import argparse
import sys
from pathlib import Path

from config.suburbs import list_suburbs
from shared.logging_config import setup_logging
from stage1_segmentation.pipeline import run_stage1


def main():
    parser = argparse.ArgumentParser(
        description="Raising Rooves — Stage 1: Roof Segmentation Pipeline"
    )
    parser.add_argument(
        "--suburb",
        type=str,
        help="Name of the Melbourne suburb to process (e.g. 'Richmond')",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=19,
        help="Map zoom level (default: 19, ~0.29 m/pixel at Melbourne latitude)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip tile download and use existing tiles in data/raw/tiles/",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging for detailed output",
    )
    parser.add_argument(
        "--max-tiles",
        type=int,
        default=None,
        help="Limit processing to first N tiles (useful for CPU smoke tests, e.g. --max-tiles 10)",
    )
    parser.add_argument(
        "--footprint-file",
        type=Path,
        default=None,
        help=(
            "Optional: path to local GeoJSON footprints file "
            "(e.g. Microsoft AU Building Footprints). "
            "If omitted, queries OSM Overpass API."
        ),
    )
    parser.add_argument(
        "--list-suburbs",
        action="store_true",
        help="List available suburbs and exit",
    )

    args = parser.parse_args()

    if args.list_suburbs:
        print("Available suburbs:")
        for name in list_suburbs():
            print(f"  - {name}")
        sys.exit(0)

    if not args.suburb:
        parser.error("--suburb is required (or use --list-suburbs)")

    # Set up logging level
    level = "DEBUG" if args.debug else "INFO"
    logger = setup_logging("stage1_cli", level=level)

    logger.info("Starting Stage 1 for suburb: %s", args.suburb)

    if args.max_tiles:
        logger.info("Smoke-test mode: capping at %d tiles.", args.max_tiles)

    try:
        df = run_stage1(
            suburb_name=args.suburb,
            zoom=args.zoom,
            skip_download=args.skip_download,
            max_tiles=args.max_tiles,
            footprint_file=args.footprint_file,
        )
        if df.empty:
            logger.warning("No results produced. Check logs for details.")
            sys.exit(1)
        else:
            logger.info("Done. %d roofs detected.", len(df))
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
