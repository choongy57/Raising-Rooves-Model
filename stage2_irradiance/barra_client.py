"""
BARRA2 climate data client for the Raising Rooves pipeline.

Accesses the Bureau of Meteorology's BARRA2 reanalysis dataset via
OPeNDAP on the NCI THREDDS server. BARRA2 provides:
  - 4km spatial resolution (vs ERA5's 31km)
  - Hourly temporal resolution
  - Variables: solar irradiance, temperature, precipitation, etc.

Data is cached locally as NetCDF files to avoid repeated downloads.
"""

from pathlib import Path

import numpy as np
import xarray as xr

from config.settings import BARRA2_THREDDS_BASE, BARRA2_VARIABLES, BARRA_DIR
from shared.file_io import ensure_dir
from shared.logging_config import setup_logging

logger = setup_logging("barra_client")


def _build_barra2_url(variable_key: str, year: int) -> str:
    """
    Build the OPeNDAP URL for a BARRA2 variable and year.

    BARRA2 data is organised by variable and time period on NCI THREDDS.
    URL structure may vary — this is the expected pattern for BARRA2
    surface analysis fields.
    """
    var_name = BARRA2_VARIABLES[variable_key]
    # BARRA2 OPeNDAP path pattern (may need adjustment based on actual NCI structure)
    return f"{BARRA2_THREDDS_BASE}/output/reanalysis/AUS-04/BOM/ERA5/historical/hres/BARRA-R2/v1/1hr/{var_name}/{var_name}_AUS-04_ERA5_historical_hres_BOM_BARRA-R2_v1_1hr_{year}.nc"


def _get_cache_path(variable_key: str, lat: float, lon: float, year: int) -> Path:
    """Get the local cache path for a downloaded BARRA2 data slice."""
    cache_dir = ensure_dir(BARRA_DIR / variable_key)
    return cache_dir / f"{variable_key}_lat{lat:.2f}_lon{lon:.2f}_{year}.nc"


def fetch_barra_data(
    variable_key: str,
    lat: float,
    lon: float,
    start_year: int,
    end_year: int,
) -> xr.Dataset | None:
    """
    Fetch BARRA2 data for a specific variable and location over a year range.

    Selects the nearest grid point to the given lat/lon. Caches data locally
    to avoid repeated OPeNDAP requests.

    Args:
        variable_key: Key from BARRA2_VARIABLES (e.g. "solar_irradiance", "temperature_2m").
        lat: Latitude (EPSG:4326).
        lon: Longitude (EPSG:4326).
        start_year: First year to fetch (inclusive).
        end_year: Last year to fetch (inclusive).

    Returns:
        xarray Dataset with the requested variable, or None if fetch fails.
    """
    var_name = BARRA2_VARIABLES.get(variable_key)
    if var_name is None:
        logger.error("Unknown variable key: '%s'. Available: %s", variable_key, list(BARRA2_VARIABLES.keys()))
        return None

    all_datasets = []

    for year in range(start_year, end_year + 1):
        # Check cache first
        cache_path = _get_cache_path(variable_key, lat, lon, year)
        if cache_path.exists():
            logger.debug("Loading cached data: %s", cache_path)
            ds = xr.open_dataset(cache_path)
            all_datasets.append(ds)
            continue

        # Fetch from OPeNDAP
        url = _build_barra2_url(variable_key, year)
        logger.info("Fetching BARRA2 %s for %d from OPeNDAP...", variable_key, year)

        try:
            ds_remote = xr.open_dataset(url, engine="netcdf4")

            # Select nearest grid point
            ds_point = ds_remote.sel(lat=lat, lon=lon, method="nearest")

            # Load into memory and cache
            ds_point = ds_point.load()
            ds_point.to_netcdf(cache_path)
            all_datasets.append(ds_point)

            logger.info("Fetched and cached BARRA2 %s for %d.", variable_key, year)

        except Exception as e:
            logger.warning(
                "Failed to fetch BARRA2 %s for %d: %s. "
                "The OPeNDAP URL pattern may need adjustment, or NCI access may be required.",
                variable_key,
                year,
                e,
            )
            continue

    if not all_datasets:
        logger.error("No BARRA2 data retrieved for %s (lat=%.4f, lon=%.4f, %d-%d)",
                      variable_key, lat, lon, start_year, end_year)
        return None

    # Combine all years
    combined = xr.concat(all_datasets, dim="time")
    logger.info(
        "BARRA2 %s: loaded %d timesteps from %d to %d.",
        variable_key,
        len(combined.time),
        start_year,
        end_year,
    )
    return combined


def fetch_all_climate_data(
    lat: float,
    lon: float,
    start_year: int = 1990,
    end_year: int = 2020,
) -> dict[str, xr.Dataset | None]:
    """
    Fetch all required climate variables for a location.

    Args:
        lat: Latitude.
        lon: Longitude.
        start_year: First year (inclusive).
        end_year: Last year (inclusive).

    Returns:
        Dict mapping variable keys to xarray Datasets (or None if failed).
    """
    results = {}
    for key in ["solar_irradiance", "temperature_2m"]:
        results[key] = fetch_barra_data(key, lat, lon, start_year, end_year)
    return results
