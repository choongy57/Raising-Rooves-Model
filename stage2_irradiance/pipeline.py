"""
Stage 2 pipeline orchestrator for the Raising Rooves pipeline.

Two responsibilities:
  1. Climate data retrieval: fetch monthly irradiance + temperature stats
     from BARRA2 (preferred) or ERA5 (fallback). Saved as stage2_{suburb}_climate.parquet.

  2. Cool roof delta: join Stage 1 buildings with annual GHI, compute per-building
     energy saving and CO2 reduction. Saved as stage2_{suburb}.parquet / .csv.

Irradiance source priority:
  a. BARRA2 via OPeNDAP (requires NCI access — connect when available)
  b. CSV file provided via --irradiance-file (lat, lon, annual_ghi_kwh_m2)
  c. Melbourne default GHI constant (~1850 kWh/m²/yr) — safe placeholder
"""

from pathlib import Path

import pandas as pd
from tqdm import tqdm

from config.settings import BARRA2_VARIABLES, ERA5_VARIABLES, MELBOURNE_DEFAULT_GHI_KWH_M2_YR, OUTPUT_DIR
from config.suburbs import get_suburb
from shared.file_io import ensure_dir, save_parquet
from shared.logging_config import setup_logging
from stage2_irradiance.barra_client import fetch_all_climate_data
from stage2_irradiance.cool_roof_calculator import calculate_building_benefit
from stage2_irradiance.era5_fallback import fetch_era5_data
from stage2_irradiance.irradiance_loader import (
    load_irradiance_csv,
    make_default_irradiance_df,
    nearest_ghi,
)
from stage2_irradiance.irradiance_processor import (
    compute_annual_irradiance_summary,
    compute_irradiance_stats,
)
from stage2_irradiance.temperature_processor import (
    compute_annual_temperature_summary,
    compute_temperature_stats,
)

logger = setup_logging("stage2_pipeline")


def _annual_ghi_from_monthly(irradiance_summary: dict) -> float | None:
    """Convert BARRA2/ERA5 monthly summary to annual kWh/m²/yr, or None if unavailable."""
    daily = irradiance_summary.get("annual_mean_ghi_kwh_m2_day")
    if daily:
        return round(daily * 365, 1)
    return None


def run_stage2_climate(
    suburb_name: str,
    start_year: int = 2010,
    end_year: int = 2020,
) -> pd.DataFrame:
    """
    Fetch monthly climate statistics for a suburb from BARRA2 (ERA5 fallback).

    Returns a DataFrame with monthly irradiance and temperature stats.
    Saved to stage2_{suburb}_climate.parquet.
    """
    suburb = get_suburb(suburb_name)
    suburb_key = suburb.name.lower().replace(" ", "_")
    lat, lon = suburb.centroid

    logger.info("=" * 60)
    logger.info("Stage 2 Climate: %s (lat=%.4f, lon=%.4f)", suburb.name, lat, lon)
    logger.info("Year range: %d-%d", start_year, end_year)
    logger.info("=" * 60)

    # ── Fetch climate data ────────────────────────────────────────────────
    logger.info("Fetching climate data (BARRA2 first, ERA5 fallback)...")
    climate_data = fetch_all_climate_data(lat, lon, start_year, end_year)

    irradiance_ds = climate_data.get("solar_irradiance")
    temperature_ds = climate_data.get("temperature_2m")
    irradiance_var = BARRA2_VARIABLES["solar_irradiance"]
    temperature_var = BARRA2_VARIABLES["temperature_2m"]

    if irradiance_ds is None:
        logger.warning("BARRA2 irradiance unavailable — falling back to ERA5...")
        irradiance_ds = fetch_era5_data("solar_irradiance", lat, lon, start_year, end_year)
        irradiance_var = ERA5_VARIABLES["solar_irradiance"]

    if temperature_ds is None:
        logger.warning("BARRA2 temperature unavailable — falling back to ERA5...")
        temperature_ds = fetch_era5_data("temperature_2m", lat, lon, start_year, end_year)
        temperature_var = ERA5_VARIABLES["temperature_2m"]

    # ── Process stats ─────────────────────────────────────────────────────
    irradiance_stats = compute_irradiance_stats(irradiance_ds, irradiance_var, suburb.name)
    irradiance_summary = compute_annual_irradiance_summary(irradiance_stats)
    if irradiance_summary:
        logger.info("Annual GHI: %.2f kWh/m²/day", irradiance_summary.get("annual_mean_ghi_kwh_m2_day", 0))

    temperature_stats = compute_temperature_stats(temperature_ds, temperature_var, suburb.name)
    temperature_summary = compute_annual_temperature_summary(temperature_stats)
    if temperature_summary:
        logger.info(
            "Annual mean temp: %.1f°C, CDD: %.0f, HDD: %.0f",
            temperature_summary.get("annual_mean_temp_c", 0),
            temperature_summary.get("annual_cdd", 0),
            temperature_summary.get("annual_hdd", 0),
        )

    if irradiance_stats.empty and temperature_stats.empty:
        logger.warning("No climate data retrieved for %s.", suburb.name)
        return pd.DataFrame()

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

    out_path = ensure_dir(OUTPUT_DIR) / f"stage2_{suburb_key}_climate.parquet"
    save_parquet(combined, out_path)
    logger.info("Climate data saved to: %s", out_path)
    return combined


def run_stage2(
    suburb_name: str,
    irradiance_file: Path | None = None,
    start_year: int = 2010,
    end_year: int = 2020,
) -> pd.DataFrame:
    """
    Run the full Stage 2 pipeline: load Stage 1 buildings, assign irradiance,
    and compute per-building cool roof benefit.

    Irradiance source priority:
      1. BARRA2 via OPeNDAP (if accessible — run_stage2_climate)
      2. CSV file at irradiance_file (lat, lon, annual_ghi_kwh_m2)
      3. Melbourne default GHI constant (~1850 kWh/m²/yr)

    Args:
        suburb_name: Suburb to process (must have a Stage 1 output).
        irradiance_file: Path to irradiance CSV, or None.
        start_year: First year for BARRA2 query (used only if BARRA2 accessible).
        end_year: Last year for BARRA2 query.

    Returns:
        DataFrame with all Stage 1 columns plus:
        annual_ghi_kwh_m2, absorptance_before, roof_surface_area_m2,
        energy_incident_kwh_yr, energy_saved_kwh_yr, co2_saved_kg_yr.
    """
    suburb = get_suburb(suburb_name)
    suburb_key = suburb.name.lower().replace(" ", "_")

    logger.info("=" * 60)
    logger.info("Stage 2 Pipeline: %s", suburb.name)
    logger.info("=" * 60)

    # ── Step 1: Load Stage 1 output ───────────────────────────────────────
    stage1_path = OUTPUT_DIR / f"stage1_{suburb_key}.parquet"
    if not stage1_path.exists():
        logger.error(
            "Stage 1 output not found: %s — run Stage 1 first.", stage1_path
        )
        return pd.DataFrame()

    df = pd.read_parquet(stage1_path)
    logger.info("Step 1/3: Loaded %d buildings from Stage 1.", len(df))

    # ── Step 2: Resolve irradiance data ───────────────────────────────────
    logger.info("Step 2/3: Resolving irradiance data...")
    annual_ghi_kwh_m2: float | None = None

    # Try BARRA2 first (will fail gracefully without NCI access)
    try:
        climate_df = run_stage2_climate(suburb_name, start_year, end_year)
        if not climate_df.empty and "mean_ghi_kwh_m2_day" in climate_df.columns:
            daily_mean = climate_df["mean_ghi_kwh_m2_day"].mean()
            annual_ghi_kwh_m2 = round(daily_mean * 365, 1)
            logger.info("Using BARRA2/ERA5 annual GHI: %.0f kWh/m²/yr", annual_ghi_kwh_m2)
    except Exception as e:
        logger.debug("BARRA2/ERA5 not available: %s", e)

    # Fall back to CSV or Melbourne default
    if annual_ghi_kwh_m2 is None:
        if irradiance_file:
            irradiance_df = load_irradiance_csv(irradiance_file)
        else:
            irradiance_df = make_default_irradiance_df(suburb.bbox)

        # Match each building to nearest grid cell
        ghi_values = [
            nearest_ghi(row["lat"], row["lon"], irradiance_df)
            for _, row in df.iterrows()
        ]
        df["annual_ghi_kwh_m2"] = ghi_values
    else:
        df["annual_ghi_kwh_m2"] = annual_ghi_kwh_m2

    # ── Step 3: Compute cool roof benefit per building ────────────────────
    logger.info("Step 3/3: Computing cool roof delta per building...")
    benefit_rows = []
    for _, row in tqdm(df.iterrows(), total=len(df), desc="Calculating benefit"):
        benefit = calculate_building_benefit(
            area_m2=float(row["area_m2"]),
            pitch_deg=float(row.get("pitch_deg", 22.5)),
            annual_ghi_kwh_m2=float(row["annual_ghi_kwh_m2"]),
            roof_colour=row.get("roof_colour"),
            roof_material=row.get("roof_material"),
        )
        benefit_rows.append(benefit)

    benefit_df = pd.DataFrame(benefit_rows)
    df = pd.concat([df.reset_index(drop=True), benefit_df], axis=1)

    # ── Summary ───────────────────────────────────────────────────────────
    total_energy_saved = df["energy_saved_kwh_yr"].sum()
    total_co2_saved = df["co2_saved_kg_yr"].sum()
    mean_absorptance = df["absorptance_before"].mean()
    total_roof_area = df["roof_surface_area_m2"].sum()

    logger.info(
        "Suburb %s: %d buildings | total roof surface %.0f m²",
        suburb.name, len(df), total_roof_area,
    )
    logger.info(
        "Cool roof benefit: %.0f kWh/yr saved | %.0f kg CO2/yr avoided | mean absorptance %.2f",
        total_energy_saved, total_co2_saved, mean_absorptance,
    )

    # ── Save outputs ──────────────────────────────────────────────────────
    out_dir = ensure_dir(OUTPUT_DIR)
    parquet_path = out_dir / f"stage2_{suburb_key}.parquet"
    csv_path = out_dir / f"stage2_{suburb_key}.csv"
    save_parquet(df, parquet_path)
    df.to_csv(csv_path, index=False)
    logger.info("Parquet: %s", parquet_path)
    logger.info("CSV:     %s", csv_path)

    logger.info("=" * 60)
    logger.info("Stage 2 complete for %s.", suburb.name)
    logger.info("=" * 60)

    return df
