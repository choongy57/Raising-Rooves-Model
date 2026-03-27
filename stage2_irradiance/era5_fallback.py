"""
ERA5 fallback data client for the Raising Rooves pipeline.

Used when BARRA2 is inaccessible (e.g. NCI account required).
ERA5 is coarser (31km vs 4km) but globally available via Copernicus CDS API.

Requires a free Copernicus CDS account and API key in .env.
"""

from pathlib import Path

import xarray as xr

from config.settings import BARRA_DIR, CDS_API_KEY, ERA5_VARIABLES
from shared.file_io import ensure_dir
from shared.logging_config import setup_logging

logger = setup_logging("era5_fallback")


def _get_era5_cache_path(variable_key: str, lat: float, lon: float, year: int) -> Path:
    """Get the local cache path for ERA5 data."""
    cache_dir = ensure_dir(BARRA_DIR / "era5" / variable_key)
    return cache_dir / f"era5_{variable_key}_lat{lat:.2f}_lon{lon:.2f}_{year}.nc"


def fetch_era5_data(
    variable_key: str,
    lat: float,
    lon: float,
    start_year: int,
    end_year: int,
) -> xr.Dataset | None:
    """
    Fetch ERA5 reanalysis data from Copernicus CDS API.

    This is a fallback for when BARRA2 is unavailable. ERA5 has lower
    spatial resolution (31km) but is freely accessible worldwide.

    Args:
        variable_key: Key from ERA5_VARIABLES ("solar_irradiance" or "temperature_2m").
        lat: Latitude (EPSG:4326).
        lon: Longitude (EPSG:4326).
        start_year: First year to fetch (inclusive).
        end_year: Last year to fetch (inclusive).

    Returns:
        xarray Dataset, or None if fetch fails.
    """
    var_name = ERA5_VARIABLES.get(variable_key)
    if var_name is None:
        logger.error("Unknown ERA5 variable key: '%s'", variable_key)
        return None

    if not CDS_API_KEY:
        logger.error(
            "CDS_API_KEY not set in .env. Sign up at https://cds.climate.copernicus.eu/ "
            "and add your key to .env"
        )
        return None

    try:
        import cdsapi
    except ImportError:
        logger.error("cdsapi package not installed. Run: pip install cdsapi")
        return None

    all_datasets = []

    for year in range(start_year, end_year + 1):
        cache_path = _get_era5_cache_path(variable_key, lat, lon, year)

        if cache_path.exists():
            logger.debug("Loading cached ERA5 data: %s", cache_path)
            ds = xr.open_dataset(cache_path)
            all_datasets.append(ds)
            continue

        logger.info("Fetching ERA5 %s for %d via CDS API...", variable_key, year)

        try:
            client = cdsapi.Client(key=CDS_API_KEY)

            # ERA5 CDS API request — monthly averaged reanalysis
            request = {
                "product_type": "monthly_averaged_reanalysis",
                "variable": var_name,
                "year": str(year),
                "month": [f"{m:02d}" for m in range(1, 13)],
                "time": "00:00",
                "area": [lat + 0.5, lon - 0.5, lat - 0.5, lon + 0.5],  # N, W, S, E
                "format": "netcdf",
            }

            # Download to temp file, then load
            temp_path = cache_path.with_suffix(".tmp.nc")
            client.retrieve(
                "reanalysis-era5-single-levels-monthly-means",
                request,
                str(temp_path),
            )

            ds = xr.open_dataset(temp_path)
            # Select nearest point
            ds_point = ds.sel(latitude=lat, longitude=lon, method="nearest")
            ds_point = ds_point.load()
            ds_point.to_netcdf(cache_path)

            # Clean up temp file
            temp_path.unlink(missing_ok=True)

            all_datasets.append(ds_point)
            logger.info("Fetched and cached ERA5 %s for %d.", variable_key, year)

        except Exception as e:
            logger.warning("Failed to fetch ERA5 %s for %d: %s", variable_key, year, e)
            continue

    if not all_datasets:
        logger.error("No ERA5 data retrieved for %s.", variable_key)
        return None

    combined = xr.concat(all_datasets, dim="time")
    logger.info("ERA5 %s: loaded %d timesteps.", variable_key, len(combined.time))
    return combined
