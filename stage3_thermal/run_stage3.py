"""
CLI entry point for Stage 3: Thermal Electricity Savings.

Converts the absorbed solar reduction from Stage 2 (energy_saved_kwh_yr) into
realistic cooling electricity savings using building thermal physics.

Usage:
    python -m stage3_thermal.run_stage3 --suburb Carlton
    python -m stage3_thermal.run_stage3 --suburb Carlton --debug
    python -m stage3_thermal.run_stage3 --list-suburbs

Prerequisites:
    Stage 2 output must exist for the suburb:
        data/output/stage2_{suburb}.parquet

Output files:
    data/output/stage3_{suburb}.parquet
    data/output/stage3_{suburb}.csv

Added columns (on top of all Stage 2 columns):
    heat_to_interior_kwh_yr       — roof heat conducted to the building interior
    cooling_load_reduction_kwh_yr — portion that drives active cooling demand
    electricity_saved_kwh_yr      — electricity saving from reduced AC load
    co2_electricity_saved_kg_yr   — CO2 avoided from the electricity saving
"""

import argparse
import sys

from config.suburbs import list_suburbs
from shared.logging_config import setup_logging
from stage3_thermal.pipeline import run_stage3


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raising Rooves — Stage 3: Thermal Electricity Savings"
    )
    parser.add_argument(
        "--suburb",
        type=str,
        help="Name of the Melbourne suburb to process (e.g. 'Carlton')",
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
    logger = setup_logging("stage3_cli", level=level)
    logger.info("Starting Stage 3 for suburb: %s", args.suburb)

    try:
        df = run_stage3(suburb_name=args.suburb)
        if df.empty:
            logger.warning("No results produced. Check logs for details.")
            sys.exit(1)

        total_elec = df["electricity_saved_kwh_yr"].sum()
        total_co2 = df["co2_electricity_saved_kg_yr"].sum()
        total_absorbed = df["energy_saved_kwh_yr"].sum()
        ratio = (total_elec / total_absorbed * 100) if total_absorbed > 0 else 0.0

        logger.info(
            "Done. %d buildings | %.0f kWh/yr electricity saved (%.1f%% of absorbed solar) | %.0f kg CO2/yr avoided.",
            len(df), total_elec, ratio, total_co2,
        )
    except Exception as e:
        logger.error("Pipeline failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
