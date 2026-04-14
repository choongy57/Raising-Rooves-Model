"""
CLI entry point for Stage 2: Cool Roof Delta Calculation.

Usage:
    # With an irradiance CSV (recommended until BARRA2 is connected):
    python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv

    # Without irradiance file — uses Melbourne default GHI (~1850 kWh/m²/yr):
    python -m stage2_irradiance.run_stage2 --suburb "Carlton"

    # Debug mode:
    python -m stage2_irradiance.run_stage2 --suburb "Carlton" --debug

    # List available suburbs:
    python -m stage2_irradiance.run_stage2 --list-suburbs

Irradiance CSV format (lat, lon, annual_ghi_kwh_m2):
    lat,lon,annual_ghi_kwh_m2
    -37.80,144.96,1852.3
    ...
"""

import argparse
import sys
from pathlib import Path

from config.suburbs import list_suburbs
from shared.logging_config import setup_logging
from stage2_irradiance.pipeline import run_stage2


def main():
    parser = argparse.ArgumentParser(
        description="Raising Rooves — Stage 2: Cool Roof Delta Calculation"
    )
    parser.add_argument(
        "--suburb",
        type=str,
        help="Name of the Melbourne suburb to process (e.g. 'Carlton')",
    )
    parser.add_argument(
        "--irradiance-file",
        type=Path,
        default=None,
        help=(
            "CSV with irradiance grid: lat, lon, annual_ghi_kwh_m2. "
            "If omitted, uses Melbourne default GHI (~1850 kWh/m²/yr)."
        ),
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2010,
        help="First year for BARRA2 query when NCI access is available (default: 2010)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2020,
        help="Last year for BARRA2 query (default: 2020)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug-level logging",
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

    level = "DEBUG" if args.debug else "INFO"
    logger = setup_logging("stage2_cli", level=level)

    if args.irradiance_file:
        logger.info("Starting Stage 2 for suburb: %s (irradiance from %s)", args.suburb, args.irradiance_file)
    else:
        logger.info(
            "Starting Stage 2 for suburb: %s (no irradiance file — using Melbourne default GHI)",
            args.suburb,
        )

    try:
        df = run_stage2(
            suburb_name=args.suburb,
            irradiance_file=args.irradiance_file,
            start_year=args.start_year,
            end_year=args.end_year,
        )
        if df.empty:
            logger.warning("No results produced. Check logs for details.")
            sys.exit(1)
        else:
            total_saved = df["energy_saved_kwh_yr"].sum()
            total_co2 = df["co2_saved_kg_yr"].sum()
            logger.info(
                "Done. %d buildings | %.0f kWh/yr saved | %.0f kg CO2/yr avoided.",
                len(df), total_saved, total_co2,
            )
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
