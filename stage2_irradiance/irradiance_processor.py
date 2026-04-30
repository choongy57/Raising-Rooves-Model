"""
Solar irradiance data processor for the Raising Rooves pipeline.

Processes raw irradiance data from BARRA2 or ERA5 into useful metrics
for cool roof modelling:
  - Annual and monthly mean Global Horizontal Irradiance (GHI)
  - Peak irradiance values
  - Seasonal patterns
"""

import numpy as np
import pandas as pd
import xarray as xr

from shared.logging_config import setup_logging

logger = setup_logging("irradiance_processor")


def compute_irradiance_stats(
    irradiance_ds: xr.Dataset,
    variable_name: str,
    suburb_name: str,
) -> pd.DataFrame:
    """
    Compute irradiance statistics from raw data.

    Args:
        irradiance_ds: xarray Dataset with irradiance variable (W/m²).
        variable_name: Name of the irradiance variable in the dataset.
        suburb_name: Suburb name for labelling output.

    Returns:
        DataFrame with columns: suburb, month, mean_ghi_w_m2,
        mean_ghi_kwh_m2_day, peak_ghi_w_m2.
    """
    if irradiance_ds is None or variable_name not in irradiance_ds:
        logger.warning("No irradiance data available for '%s'.", suburb_name)
        return pd.DataFrame()

    data = irradiance_ds[variable_name]

    # Group by month across all years
    monthly = data.groupby("time.month")

    records = []
    for month, group in monthly:
        mean_w_m2 = float(group.mean().values)
        peak_w_m2 = float(group.max().values)

        # Convert W/m² average to daily kWh/m²/day
        # Assuming the value represents average flux, and ~12 effective sun hours for monthly mean
        # More precisely: daily_kwh = mean_W * peak_sun_hours / 1000
        # For Melbourne: ~5.5 peak sun hours summer, ~2.5 winter
        # Simple approximation: mean_W * 24 * fraction_of_day / 1000
        # BARRA2 gives instantaneous or averaged flux, so we approximate:
        mean_kwh_m2_day = mean_w_m2 * 24 / 1000  # rough conversion

        records.append({
            "suburb": suburb_name,
            "month": int(month),
            "mean_ghi_w_m2": round(mean_w_m2, 2),
            "mean_ghi_kwh_m2_day": round(mean_kwh_m2_day, 2),
            "peak_ghi_w_m2": round(peak_w_m2, 2),
        })

    df = pd.DataFrame(records)

    if not df.empty:
        annual_mean = df["mean_ghi_kwh_m2_day"].mean()
        logger.info(
            "Irradiance stats for '%s': annual mean GHI = %.2f kWh/m²/day",
            suburb_name,
            annual_mean,
        )

    return df


def compute_annual_ghi_from_hourly(
    ds: xr.Dataset,
    variable: str = "rsds",
    years: list[int] | None = None,
) -> float:
    """
    Convert hourly BARRA2 rsds (W/m²) to annual mean GHI (kWh/m²/yr).

    BARRA2 rsds is the mean downward shortwave flux over the 1-hour output
    interval (W/m²).  To convert to annual energy:

        annual kWh/m²/yr = mean(W/m²) × 8760 h/yr ÷ 1000

    If multiple years are present in the dataset, the mean W/m² is computed
    per year and then averaged across years before the final conversion.  This
    avoids biasing the result toward years with more timesteps.

    Args:
        ds: xarray Dataset containing the irradiance variable.
        variable: Name of the irradiance variable (default "rsds").
        years: Optional list of years to restrict the calculation.
               If None, all years present in the dataset are used.

    Returns:
        Annual mean GHI in kWh/m²/yr, rounded to one decimal place.

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

    if years is not None:
        da = da.sel(time=da["time.year"].isin(years))

    if da.size == 0:
        raise ValueError("Dataset contains no timesteps after year filtering.")

    # Compute mean W/m² per calendar year, then average across years.
    year_labels = da["time.year"].values
    unique_years = sorted(set(int(y) for y in year_labels))

    yearly_means: list[float] = []
    for yr in unique_years:
        mask = da["time.year"] == yr
        yr_data = da.sel(time=mask)
        valid = yr_data.values[~np.isnan(yr_data.values)]
        if len(valid) == 0:
            logger.warning("No valid rsds values for year %d — skipping.", yr)
            continue
        yearly_means.append(float(valid.mean()))

    if not yearly_means:
        raise ValueError("No valid annual means could be computed (all NaN).")

    mean_w_m2 = float(np.mean(yearly_means))
    annual_kwh = mean_w_m2 * 8760 / 1000

    logger.info(
        "compute_annual_ghi_from_hourly: mean %.2f W/m² over %d year(s) → %.1f kWh/m²/yr",
        mean_w_m2,
        len(yearly_means),
        annual_kwh,
    )
    return round(annual_kwh, 1)


def compute_annual_irradiance_summary(monthly_stats: pd.DataFrame) -> dict:
    """
    Compute annual summary from monthly irradiance stats.

    Args:
        monthly_stats: DataFrame from compute_irradiance_stats().

    Returns:
        Dict with annual summary metrics.
    """
    if monthly_stats.empty:
        return {}

    return {
        "annual_mean_ghi_kwh_m2_day": round(monthly_stats["mean_ghi_kwh_m2_day"].mean(), 2),
        "summer_mean_ghi_kwh_m2_day": round(
            monthly_stats[monthly_stats["month"].isin([12, 1, 2])]["mean_ghi_kwh_m2_day"].mean(), 2
        ),
        "winter_mean_ghi_kwh_m2_day": round(
            monthly_stats[monthly_stats["month"].isin([6, 7, 8])]["mean_ghi_kwh_m2_day"].mean(), 2
        ),
        "peak_ghi_w_m2": round(monthly_stats["peak_ghi_w_m2"].max(), 2),
    }
