"""
BARRA2 climate data client for the Raising Rooves pipeline.

Accesses the Bureau of Meteorology's BARRA2 reanalysis dataset via
OPeNDAP on the NCI THREDDS server. BARRA2 provides:
  - ~11 km spatial resolution (AUS-11 BARRA-R2 grid)
  - Hourly temporal resolution
  - Variables: solar irradiance (rsds), temperature (tas), precipitation, etc.

Data is cached locally as NetCDF files to avoid repeated downloads.

NCI account required. Monash students: register at https://my.nci.org.au
and ask your supervisor (Stuart) for project ob53 access.

URL structure (verified against NCI THREDDS catalog 2026-04-30):
  OPeNDAP base:  https://thredds.nci.org.au/thredds/dodsC/ob53
  Catalog base:  https://thredds.nci.org.au/thredds/catalog/ob53
  Path template: output/reanalysis/{domain}/BOM/ERA5/historical/hres/BARRA-R2/v1/
                 1hr/{variable}/{version}/{variable}_{domain}_ERA5_historical_hres_BOM_BARRA-R2_v1_1hr_{YYYYMM}-{YYYYMM}.nc

  Notes:
  - Domain for BARRA-R2 is AUS-11 (11 km), NOT AUS-04 (that is BARRA-C2).
  - The THREDDS path does NOT include the 'BARRA2/' product subfolder that
    exists on the gdata filesystem.
  - Variable names are CORDEX/CF names (rsds, tas) not UM internal names
    (av_swsfcdown, temp_scrn).
  - Each file covers one calendar month. Use 'latest' as the version token
    to resolve the most recent data release without knowing the exact date tag.
"""

import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import xarray as xr

from config.settings import (
    BARRA2_CATALOG_BASE,
    BARRA2_DOMAIN,
    BARRA2_THREDDS_BASE,
    BARRA2_VARIABLES,
    BARRA_DIR,
)
from shared.file_io import ensure_dir
from shared.logging_config import setup_logging

logger = setup_logging("barra_client")

# Connection test timeout in seconds — short so the pipeline fails fast.
_CONNECTION_TIMEOUT_S = 8


def _build_barra2_url(variable_key: str, year: int, month: int = 1) -> str:
    """
    Build the OPeNDAP URL for a BARRA2 variable, year, and month.

    BARRA2 files on NCI THREDDS are one file per calendar month.  The 'latest'
    version token resolves to the most recently published data release without
    requiring the caller to know the exact release date tag (e.g. v20231001).

    Path components (all confirmed from live catalog 2026-04-30):
      ob53                   — NCI project code, top-level directory on THREDDS
      output/reanalysis      — product type (model output, reanalysis)
      {domain}               — AUS-11 (BARRA-R2 11 km grid)
      BOM                    — RCM institution
      ERA5                   — lateral boundary driving model
      historical/hres        — experiment / variant label
      BARRA-R2               — source model
      v1                     — version realisation
      1hr                    — temporal frequency
      {variable}             — CORDEX/CF variable name (e.g. rsds, tas)
      latest                 — version folder (points to most recent release)
      {filename}             — one file per month

    Args:
        variable_key: Key from BARRA2_VARIABLES (e.g. "solar_irradiance").
        year: Calendar year (e.g. 2015).
        month: Calendar month 1–12 (default 1).

    Returns:
        OPeNDAP URL string.
    """
    var_name = BARRA2_VARIABLES[variable_key]
    yyyymm = f"{year}{month:02d}"
    filename = (
        f"{var_name}_{BARRA2_DOMAIN}_ERA5_historical_hres_BOM_BARRA-R2_v1"
        f"_1hr_{yyyymm}-{yyyymm}.nc"
    )
    return (
        f"{BARRA2_THREDDS_BASE}/output/reanalysis/{BARRA2_DOMAIN}"
        f"/BOM/ERA5/historical/hres/BARRA-R2/v1/1hr/{var_name}/latest/{filename}"
    )


def _build_barra2_catalog_url(variable_key: str) -> str:
    """
    Return the THREDDS catalog HTML URL for a BARRA2 variable.

    Useful for debugging without NCI OPeNDAP access — the catalog page is
    publicly browsable even when OPeNDAP requires authentication.

    Args:
        variable_key: Key from BARRA2_VARIABLES.

    Returns:
        THREDDS catalog URL string.
    """
    var_name = BARRA2_VARIABLES[variable_key]
    return (
        f"{BARRA2_CATALOG_BASE}/output/reanalysis/{BARRA2_DOMAIN}"
        f"/BOM/ERA5/historical/hres/BARRA-R2/v1/1hr/{var_name}/catalog.html"
    )


def test_barra2_connection() -> bool:
    """
    Check whether the NCI THREDDS server is reachable.

    Tries to open the THREDDS catalog URL (not OPeNDAP) with a short timeout.
    This does not require NCI authentication, so a True result only confirms
    that the server is reachable — not that OPeNDAP data access will succeed.

    Returns:
        True if the catalog URL responds with HTTP 200, False otherwise.
    """
    url = _build_barra2_catalog_url("solar_irradiance")
    logger.info("Testing BARRA2 connection: %s", url)
    try:
        with urllib.request.urlopen(url, timeout=_CONNECTION_TIMEOUT_S) as resp:
            if resp.status == 200:
                logger.info("BARRA2 THREDDS catalog reachable (HTTP 200).")
                return True
            logger.warning(
                "BARRA2 THREDDS catalog returned HTTP %d — unexpected.", resp.status
            )
            return False
    except urllib.error.HTTPError as e:
        logger.warning("BARRA2 THREDDS catalog HTTP error %d: %s", e.code, e.reason)
    except urllib.error.URLError as e:
        logger.warning(
            "BARRA2 THREDDS catalog unreachable: %s. "
            "Check network connectivity or NCI VPN.",
            e.reason,
        )
    except OSError as e:
        logger.warning("BARRA2 connection test failed: %s", e)
    return False


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

    Selects the nearest grid point to the given lat/lon.  Caches data locally
    to avoid repeated OPeNDAP requests.  Fails gracefully when NCI is not
    accessible — returns None and logs a WARNING rather than raising.

    BARRA2 files are one file per calendar month.  This function fetches all
    12 months for each year in [start_year, end_year] and concatenates them
    along the time dimension.

    Args:
        variable_key: Key from BARRA2_VARIABLES (e.g. "solar_irradiance").
        lat: Latitude in EPSG:4326.
        lon: Longitude in EPSG:4326.
        start_year: First year to fetch (inclusive).
        end_year: Last year to fetch (inclusive).

    Returns:
        xarray Dataset with the requested variable, or None if fetch fails.
    """
    var_name = BARRA2_VARIABLES.get(variable_key)
    if var_name is None:
        logger.error(
            "Unknown variable key: '%s'. Available: %s",
            variable_key,
            list(BARRA2_VARIABLES.keys()),
        )
        return None

    all_datasets: list[xr.Dataset] = []

    for year in range(start_year, end_year + 1):
        # Check for a year-level cache first
        cache_path = _get_cache_path(variable_key, lat, lon, year)
        if cache_path.exists():
            logger.debug("Loading cached data: %s", cache_path)
            all_datasets.append(xr.open_dataset(cache_path))
            continue

        # Fetch all 12 months from OPeNDAP and concatenate into one year
        monthly_slices: list[xr.Dataset] = []
        for month in range(1, 13):
            url = _build_barra2_url(variable_key, year, month)
            logger.info(
                "Fetching BARRA2 %s %d-%02d from OPeNDAP...", variable_key, year, month
            )
            try:
                ds_remote = xr.open_dataset(url, engine="netcdf4")
                ds_point = ds_remote.sel(lat=lat, lon=lon, method="nearest")
                ds_point = ds_point.load()
                monthly_slices.append(ds_point)
            except Exception as e:
                logger.warning(
                    "Failed to fetch BARRA2 %s %d-%02d: %s. "
                    "NCI OPeNDAP access may require authentication — "
                    "register at https://my.nci.org.au and request ob53 access.",
                    variable_key,
                    year,
                    month,
                    e,
                )

        if not monthly_slices:
            logger.warning(
                "No monthly data retrieved for BARRA2 %s %d — skipping year.",
                variable_key,
                year,
            )
            continue

        # Concatenate months → annual dataset, cache to disk
        ds_year = xr.concat(monthly_slices, dim="time")
        try:
            ds_year.to_netcdf(cache_path)
            logger.info("Cached BARRA2 %s %d → %s", variable_key, year, cache_path)
        except Exception as e:
            logger.warning("Could not cache BARRA2 %s %d: %s", variable_key, year, e)

        all_datasets.append(ds_year)

    if not all_datasets:
        logger.warning(
            "No BARRA2 data retrieved for %s (lat=%.4f, lon=%.4f, %d–%d). "
            "Catalog URL for manual inspection: %s",
            variable_key,
            lat,
            lon,
            start_year,
            end_year,
            _build_barra2_catalog_url(variable_key),
        )
        return None

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
        lat: Latitude in EPSG:4326.
        lon: Longitude in EPSG:4326.
        start_year: First year (inclusive).
        end_year: Last year (inclusive).

    Returns:
        Dict mapping variable keys to xarray Datasets (or None if failed).
    """
    results: dict[str, xr.Dataset | None] = {}
    for key in ["solar_irradiance", "temperature_2m"]:
        results[key] = fetch_barra_data(key, lat, lon, start_year, end_year)
    return results
