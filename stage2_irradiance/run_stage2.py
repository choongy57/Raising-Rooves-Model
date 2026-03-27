"""
CLI entry point for Stage 2: Irradiance & Climate Data.

Usage:
    python -m stage2_irradiance.run_stage2 --suburb "Richmond"
    python -m stage2_irradiance.run_stage2 --suburb "Richmond" --debug
    python -m stage2_irradiance.run_stage2 --suburb "Richmond" --start-year 2000 --end-year 2020
    python -m stage2_irradiance.run_stage2 --list-suburbs
"""

import argparse
import sys

from config.suburbs import list_suburbs
from shared.logging_config import setup_logging
from stage2_irradiance.pipeline import run_stage2


def main():
    parser = argparse.ArgumentParser(
        description="Raising Rooves — Stage 2: Irradiance & Climate Data Pipeline"
    )
    parser.add_argument(
        "--suburb",
        type=str,
        help="Name of the Melbourne suburb to process (e.g. 'Richmond')",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1990,
        help="First year for climate data (default: 1990)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2020,
        help="Last year for climate data (default: 2020)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging for detailed output",
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
    logger = setup_logging("stage2_cli", level=level)

    logger.info("Starting Stage 2 for suburb: %s (%d-%d)", args.suburb, args.start_year, args.end_year)

    try:
        df = run_stage2(
            suburb_name=args.suburb,
            start_year=args.start_year,
            end_year=args.end_year,
        )
        if df.empty:
            logger.warning("No results produced. Check logs for details.")
            sys.exit(1)
        else:
            logger.info("Done. %d monthly records produced.", len(df))
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
