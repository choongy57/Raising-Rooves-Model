"""
Stage 2 pipeline orchestrator for the Raising Rooves pipeline.

Chains together: climate data fetch → irradiance processing → temperature processing.
Tries BARRA2 first, falls back to ERA5 if BARRA2 is unavailable.
"""

import pandas as pd

from config.settings import BARRA2_VARIABLES, ERA5_VARIABLES, OUTPUT_DIR
from config.suburbs import get_suburb
from shared.file_io import ensure_dir, save_parquet
from shared.logging_config import setup_logging
from stage2_irradiance.barra_client import fetch_all_climate_data
from stage2_irradiance.era5_fallback import fetch_era5_data
from stage2_irradiance.irradiance_processor import (
    compute_annual_irradiance_summary,
    compute_irradiance_stats,
)
from stage2_irradiance.temperature_processor import (
    compute_annual_temperature_summary,
    compute_temperature_stats,
)

logger = setup_logging("stage2_pipeline")


def run_stage2(
    suburb_name: str,
    start_year: int = 1990,
    end_year: int = 2020,
) -> pd.DataFrame:
    """
    Run the full Stage 2 pipeline for a single suburb.

    Steps:
        1. Look up suburb centroid coordinates
        2. Fetch climate data (BARRA2 first, ERA5 fallback)
        3. Process irradiance statistics
        4. Process temperature statistics
        5. Combine and save results

    Args:
        suburb_name: Name of the suburb (must be in config/suburbs.py).
        start_year: First year for climate data (inclusive).
        end_year: Last year for climate data (inclusive).

    Returns:
        DataFrame with monthly climate statistics for the suburb.
    """
    suburb = get_suburb(suburb_name)
    suburb_key = suburb.name.lower().replace(" ", "_")
    lat, lon = suburb.centroid

    logger.info("=" * 60)
    logger.info("Stage 2 Pipeline: %s (lat=%.4f, lon=%.4f)", suburb.name, lat, lon)
    logger.info("Year range: %d-%d", start_year, end_year)
    logger.info("=" * 60)

    # ── Step 1: Fetch climate data ───────────────────────────────────────
    logger.info("Step 1/3: Fetching climate data (trying BARRA2 first)...")

    climate_data = fetch_all_climate_data(lat, lon, start_year, end_year)

    # Check if we got data; fall back to ERA5 if needed
    irradiance_ds = climate_data.get("solar_irradiance")
    temperature_ds = climate_data.get("temperature_2m")
    irradiance_var = BARRA2_VARIABLES["solar_irradiance"]
    temperature_var = BARRA2_VARIABLES["temperature_2m"]

    if irradiance_ds is None:
        logger.warning("BARRA2 irradiance unavailable. Falling back to ERA5...")
        irradiance_ds = fetch_era5_data("solar_irradiance", lat, lon, start_year, end_year)
        irradiance_var = ERA5_VARIABLES["solar_irradiance"]

    if temperature_ds is None:
        logger.warning("BARRA2 temperature unavailable. Falling back to ERA5...")
        temperature_ds = fetch_era5_data("temperature_2m", lat, lon, start_year, end_year)
        temperature_var = ERA5_VARIABLES["temperature_2m"]

    # ── Step 2: Process irradiance ───────────────────────────────────────
    logger.info("Step 2/3: Processing irradiance data...")
    irradiance_stats = compute_irradiance_stats(irradiance_ds, irradiance_var, suburb.name)
    irradiance_summary = compute_annual_irradiance_summary(irradiance_stats)

    if irradiance_summary:
        logger.info("Annual GHI: %.2f kWh/m²/day", irradiance_summary.get("annual_mean_ghi_kwh_m2_day", 0))

    # ── Step 3: Process temperature ──────────────────────────────────────
    logger.info("Step 3/3: Processing temperature data...")
    temperature_stats = compute_temperature_stats(temperature_ds, temperature_var, suburb.name)
    temperature_summary = compute_annual_temperature_summary(temperature_stats)

    if temperature_summary:
        logger.info("Annual mean temp: %.1f°C, CDD: %.0f, HDD: %.0f",
                     temperature_summary.get("annual_mean_temp_c", 0),
                     temperature_summary.get("annual_cdd", 0),
                     temperature_summary.get("annual_hdd", 0))

    # ── Combine results ──────────────────────────────────────────────────
    if irradiance_stats.empty and temperature_stats.empty:
        logger.warning("No climate data processed for '%s'.", suburb.name)
        return pd.DataFrame()

    # Merge irradiance and temperature on suburb + month
    if not irradiance_stats.empty and not temperature_stats.empty:
        combined = pd.merge(
            irradiance_stats,
            temperature_stats.drop(columns=["suburb"], errors="ignore"),
            on="month",
            how="outer",
        )
    elif not irradiance_stats.empty:
        combined = irradiance_stats
    else:
        combined = temperature_stats

    combined["suburb"] = suburb.name

    # Save results
    output_path = ensure_dir(OUTPUT_DIR) / f"stage2_{suburb_key}.parquet"
    save_parquet(combined, output_path)
    logger.info("Results saved to: %s", output_path)

    logger.info("Stage 2 complete for %s.", suburb.name)
    return combined
