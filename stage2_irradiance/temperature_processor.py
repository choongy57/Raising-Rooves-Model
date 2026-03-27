"""
Temperature data processor for the Raising Rooves pipeline.

Processes temperature data from BARRA2 or ERA5 into metrics relevant
to cool roof modelling:
  - Monthly and annual mean temperature
  - Cooling Degree Days (CDD) — proxy for cooling energy demand
  - Heating Degree Days (HDD) — proxy for heating energy demand
  - Heatwave frequency
"""

import numpy as np
import pandas as pd
import xarray as xr

from config.settings import CDD_BASE_TEMP, HDD_BASE_TEMP
from shared.logging_config import setup_logging

logger = setup_logging("temperature_processor")

# BARRA2 and ERA5 store temperature in Kelvin
KELVIN_OFFSET = 273.15


def _kelvin_to_celsius(temp_k: float) -> float:
    """Convert temperature from Kelvin to Celsius."""
    return temp_k - KELVIN_OFFSET


def compute_temperature_stats(
    temperature_ds: xr.Dataset,
    variable_name: str,
    suburb_name: str,
) -> pd.DataFrame:
    """
    Compute temperature statistics from raw data.

    Args:
        temperature_ds: xarray Dataset with temperature variable (Kelvin).
        variable_name: Name of the temperature variable in the dataset.
        suburb_name: Suburb name for labelling output.

    Returns:
        DataFrame with columns: suburb, month, mean_temp_c, max_temp_c,
        min_temp_c, cdd, hdd.
    """
    if temperature_ds is None or variable_name not in temperature_ds:
        logger.warning("No temperature data available for '%s'.", suburb_name)
        return pd.DataFrame()

    data = temperature_ds[variable_name]

    # Convert to Celsius if data appears to be in Kelvin
    sample_val = float(data.isel(time=0).values)
    if sample_val > 100:  # likely Kelvin
        data = data - KELVIN_OFFSET
        logger.debug("Converted temperature from Kelvin to Celsius.")

    # Group by month across all years
    monthly = data.groupby("time.month")

    records = []
    for month, group in monthly:
        mean_temp = float(group.mean().values)
        max_temp = float(group.max().values)
        min_temp = float(group.min().values)

        # Cooling Degree Days: sum of (T - T_base) for days where T > T_base
        # Approximation from monthly mean: CDD ≈ max(0, mean_T - base) * days_in_month
        days_in_month = 30  # approximation
        cdd = max(0, mean_temp - CDD_BASE_TEMP) * days_in_month
        hdd = max(0, HDD_BASE_TEMP - mean_temp) * days_in_month

        records.append({
            "suburb": suburb_name,
            "month": int(month),
            "mean_temp_c": round(mean_temp, 2),
            "max_temp_c": round(max_temp, 2),
            "min_temp_c": round(min_temp, 2),
            "cdd": round(cdd, 1),
            "hdd": round(hdd, 1),
        })

    df = pd.DataFrame(records)

    if not df.empty:
        annual_mean = df["mean_temp_c"].mean()
        annual_cdd = df["cdd"].sum()
        annual_hdd = df["hdd"].sum()
        logger.info(
            "Temperature stats for '%s': annual mean = %.1f°C, CDD = %.0f, HDD = %.0f",
            suburb_name,
            annual_mean,
            annual_cdd,
            annual_hdd,
        )

    return df


def compute_annual_temperature_summary(monthly_stats: pd.DataFrame) -> dict:
    """
    Compute annual temperature summary from monthly stats.

    Args:
        monthly_stats: DataFrame from compute_temperature_stats().

    Returns:
        Dict with annual summary metrics.
    """
    if monthly_stats.empty:
        return {}

    return {
        "annual_mean_temp_c": round(monthly_stats["mean_temp_c"].mean(), 1),
        "annual_max_temp_c": round(monthly_stats["max_temp_c"].max(), 1),
        "annual_min_temp_c": round(monthly_stats["min_temp_c"].min(), 1),
        "annual_cdd": round(monthly_stats["cdd"].sum(), 0),
        "annual_hdd": round(monthly_stats["hdd"].sum(), 0),
        "summer_mean_temp_c": round(
            monthly_stats[monthly_stats["month"].isin([12, 1, 2])]["mean_temp_c"].mean(), 1
        ),
        "winter_mean_temp_c": round(
            monthly_stats[monthly_stats["month"].isin([6, 7, 8])]["mean_temp_c"].mean(), 1
        ),
    }
