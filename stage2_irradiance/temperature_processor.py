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


def compute_cooling_degree_hours(
    ds: xr.Dataset,
    variable: str = "tas",
    base_temp_c: float = 18.5,
) -> float:
    """
    Compute mean annual Cooling Degree Hours (CDH) from hourly BARRA2 screen temperature.

    BARRA2 tas (near-surface air temperature) is stored in Kelvin.  This
    function converts to Celsius first, then sums the positive exceedances
    above the base temperature for every hourly timestep.

        CDH_year = sum(max(0, T_hourly_C - base_temp_c)) for all hours in year
        result   = mean(CDH_year) across all years in the dataset

    The Australian standard cooling base temperature of 18.5°C is used by
    default (AS/NZS 4356:2003).

    Args:
        ds: xarray Dataset containing the temperature variable in Kelvin.
        variable: Name of the temperature variable (default "tas").
        base_temp_c: Cooling base temperature in °C (default 18.5).

    Returns:
        Mean annual CDH in degree-hours, rounded to one decimal place.

    Raises:
        KeyError: If *variable* is not present in *ds*.
        ValueError: If the dataset contains no valid (non-NaN) data.
    """
    if variable not in ds:
        raise KeyError(
            f"Variable '{variable}' not found in dataset. "
            f"Available variables: {list(ds.data_vars)}"
        )

    da = ds[variable]

    if da.size == 0:
        raise ValueError("Dataset contains no timesteps.")

    # Convert from Kelvin to Celsius if the data looks like Kelvin.
    sample_val = float(da.isel(time=0).values.flat[0])
    if sample_val > 100:
        da = da - KELVIN_OFFSET
        logger.debug(
            "compute_cooling_degree_hours: converted temperature from Kelvin to Celsius."
        )

    unique_years = sorted(set(int(y) for y in da["time.year"].values))

    yearly_cdh: list[float] = []
    for yr in unique_years:
        mask = da["time.year"] == yr
        yr_data = da.sel(time=mask).values
        valid = yr_data[~np.isnan(yr_data)]
        if len(valid) == 0:
            logger.warning("No valid temperature values for year %d — skipping.", yr)
            continue
        cdh = float(np.sum(np.maximum(0.0, valid - base_temp_c)))
        yearly_cdh.append(cdh)

    if not yearly_cdh:
        raise ValueError("No valid annual CDH values could be computed (all NaN).")

    mean_cdh = float(np.mean(yearly_cdh))
    logger.info(
        "compute_cooling_degree_hours: mean %.1f CDH/yr (base %.1f°C, %d year(s)).",
        mean_cdh,
        base_temp_c,
        len(yearly_cdh),
    )
    return round(mean_cdh, 1)


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
