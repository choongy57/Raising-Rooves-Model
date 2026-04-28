"""
CLI for the opt-in Gemini + OSM Stage 1 experiment.

Example:
    python -m tools.run_gemini_osm_experiment --suburb Clayton --max-buildings 5
"""

from __future__ import annotations

import argparse
import sys

from shared.logging_config import setup_logging
from stage1_segmentation.gemini_osm_experiment import (
    DEFAULT_RATE_LIMIT_DELAY_SECONDS,
    GEMINI_OSM_MODEL,
    run_gemini_osm_experiment,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Experimental Gemini+OSM roof assessment. Reads existing Stage 1 "
            "outputs and writes separate comparison files under data/output/experiments."
        )
    )
    parser.add_argument("--suburb", required=True, help="Suburb with existing Stage 1 outputs")
    parser.add_argument("--zoom", type=int, default=19, help="Tile zoom level used by Stage 1")
    parser.add_argument(
        "--max-buildings",
        type=int,
        default=10,
        help="Maximum buildings to send to Gemini. Keep this small while validating.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=0,
        help="Stage 1 row index to start from, useful for sampling/resuming.",
    )
    parser.add_argument(
        "--rate-limit-delay",
        type=float,
        default=DEFAULT_RATE_LIMIT_DELAY_SECONDS,
        help="Seconds to wait between Gemini API calls.",
    )
    parser.add_argument("--model", default=GEMINI_OSM_MODEL, help="Gemini model name")
    parser.add_argument(
        "--media-resolution",
        choices=["low", "medium", "high"],
        default="high",
        help="Gemini media resolution for image analysis. High is best for small roof details.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build crops and output placeholders without calling Gemini.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete existing experiment outputs for this suburb before running.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    logger = setup_logging("gemini_osm_cli", level="DEBUG" if args.debug else "INFO")

    try:
        df = run_gemini_osm_experiment(
            suburb_name=args.suburb,
            zoom=args.zoom,
            max_buildings=args.max_buildings,
            start_index=args.start_index,
            rate_limit_delay=args.rate_limit_delay,
            model=args.model,
            media_resolution=args.media_resolution,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Gemini+OSM experiment failed: %s", exc, exc_info=args.debug)
        sys.exit(1)

    logger.info("Completed %d new Gemini+OSM assessments.", len(df))


if __name__ == "__main__":
    main()
