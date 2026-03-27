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
