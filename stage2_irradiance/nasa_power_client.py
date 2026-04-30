"""
NASA POWER API client for the Raising Rooves pipeline.

Fetches annual GHI (Global Horizontal Irradiance) from NASA's POWER
(Prediction Of Worldwide Energy Resources) Climatology API.

API details:
    - Endpoint: https://power.larc.nasa.gov/api/temporal/climatology/point
    - Parameter: ALLSKY_SFC_SW_DWN (kWh/m²/day, all-sky surface shortwave downward)
    - Community: RE (Renewable Energy)
    - Resolution: ~50 km — sufficient for suburb-level analysis
    - No API key required; no registration needed

Conversion: kWh/m²/day × 365 = kWh/m²/yr

Caching:
    Results are written to data/raw/nasa_power/{suburb_key}_ghi.csv so that
    repeat runs do not hit the NASA API. Delete the cache file to force a
    fresh fetch.
"""

import logging
import time
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_NASA_POWER_BASE = (
    "https://power.larc.nasa.gov/api/temporal/climatology/point"
)
_PARAMETER = "ALLSKY_SFC_SW_DWN"
_COMMUNITY = "RE"
_REQUEST_TIMEOUT_S = 30
_RETRY_WAIT_S = 2.0
_MAX_RETRIES = 3


def fetch_annual_ghi(lat: float, lon: float) -> float | None:
    """
    Fetch annual mean GHI (kWh/m²/yr) from NASA POWER for a single point.

    The API returns monthly climatology values in kWh/m²/day.  The annual
    mean (key "ANN") is multiplied by 365 to convert to kWh/m²/yr.

    Args:
        lat: Latitude in decimal degrees (WGS84).
        lon: Longitude in decimal degrees (WGS84).

    Returns:
        Annual GHI in kWh/m²/yr, or None if the request fails.
    """
    params = {
        "parameters": _PARAMETER,
        "community": _COMMUNITY,
        "longitude": lon,
        "latitude": lat,
        "format": "JSON",
    }

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.debug(
                "NASA POWER request (attempt %d/%d): lat=%.4f lon=%.4f",
                attempt, _MAX_RETRIES, lat, lon,
            )
            resp = requests.get(
                _NASA_POWER_BASE, params=params, timeout=_REQUEST_TIMEOUT_S
            )
            resp.raise_for_status()
            data = resp.json()

            # Navigate: properties → parameter → ALLSKY_SFC_SW_DWN → ANN
            ann_daily = (
                data
                .get("properties", {})
                .get("parameter", {})
                .get(_PARAMETER, {})
                .get("ANN")
            )

            if ann_daily is None:
                logger.warning(
                    "NASA POWER response missing ANN key for lat=%.4f lon=%.4f. "
                    "Full parameter keys: %s",
                    lat, lon,
                    list(
                        data.get("properties", {})
                        .get("parameter", {})
                        .get(_PARAMETER, {})
                        .keys()
                    ),
                )
                return None

            # -999 is NASA POWER's fill/missing value
            if float(ann_daily) < 0:
                logger.warning(
                    "NASA POWER returned fill value (%.1f) for lat=%.4f lon=%.4f — "
                    "point may be outside data coverage.",
                    ann_daily, lat, lon,
                )
                return None

            annual_ghi = round(float(ann_daily) * 365, 1)
            logger.debug(
                "NASA POWER: lat=%.4f lon=%.4f → %.2f kWh/m²/day → %.1f kWh/m²/yr",
                lat, lon, ann_daily, annual_ghi,
            )
            return annual_ghi

        except requests.exceptions.Timeout:
            logger.warning(
                "NASA POWER request timed out (attempt %d/%d) for lat=%.4f lon=%.4f.",
                attempt, _MAX_RETRIES, lat, lon,
            )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "NASA POWER request error (attempt %d/%d) for lat=%.4f lon=%.4f: %s",
                attempt, _MAX_RETRIES, lat, lon, exc,
            )

        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_WAIT_S)

    logger.error(
        "NASA POWER: all %d attempts failed for lat=%.4f lon=%.4f. "
        "Returning None.",
        _MAX_RETRIES, lat, lon,
    )
    return None


def fetch_suburb_ghi_grid(
    south: float,
    west: float,
    north: float,
    east: float,
    grid_spacing_deg: float = 0.1,
) -> pd.DataFrame:
    """
    Sample NASA POWER GHI on a grid across a suburb bounding box.

    Grid points are placed at every `grid_spacing_deg` degrees starting from
    the centroid of the bbox.  At minimum, the centroid itself is sampled so
    that even very small suburbs return at least one data point.

    NASA POWER has ~50 km resolution; a 0.1° spacing (~11 km) ensures we
    capture spatial variation across larger suburbs without over-querying the
    API.

    Args:
        south: Southern latitude of the bounding box.
        west:  Western longitude of the bounding box.
        north: Northern latitude of the bounding box.
        east:  Eastern longitude of the bounding box.
        grid_spacing_deg: Grid cell size in decimal degrees (default 0.1°).

    Returns:
        DataFrame with columns: lat, lon, annual_ghi_kwh_m2.
        Empty DataFrame if all requests fail.
    """
    import math

    # Build lat/lon grid, always including the centroid
    centre_lat = (south + north) / 2.0
    centre_lon = (west + east) / 2.0

    lat_span = north - south
    lon_span = east - west

    # Number of steps each side of centre (rounded up so bbox is covered)
    n_lat = max(1, math.ceil(lat_span / grid_spacing_deg / 2))
    n_lon = max(1, math.ceil(lon_span / grid_spacing_deg / 2))

    lats = sorted({
        round(centre_lat + i * grid_spacing_deg, 6)
        for i in range(-n_lat, n_lat + 1)
        if south <= centre_lat + i * grid_spacing_deg <= north
    } | {round(centre_lat, 6)})

    lons = sorted({
        round(centre_lon + j * grid_spacing_deg, 6)
        for j in range(-n_lon, n_lon + 1)
        if west <= centre_lon + j * grid_spacing_deg <= east
    } | {round(centre_lon, 6)})

    total_points = len(lats) * len(lons)
    logger.info(
        "NASA POWER grid: %d lat × %d lon = %d sample points "
        "(spacing=%.2f°, bbox=[%.4f,%.4f,%.4f,%.4f])",
        len(lats), len(lons), total_points,
        grid_spacing_deg, south, west, north, east,
    )

    rows = []
    for lat in lats:
        for lon in lons:
            ghi = fetch_annual_ghi(lat, lon)
            if ghi is not None:
                rows.append({"lat": lat, "lon": lon, "annual_ghi_kwh_m2": ghi})
            else:
                logger.warning(
                    "Skipping grid point lat=%.4f lon=%.4f — fetch returned None.",
                    lat, lon,
                )

    if not rows:
        logger.error(
            "NASA POWER: no successful fetches for bbox "
            "[south=%.4f west=%.4f north=%.4f east=%.4f].",
            south, west, north, east,
        )
        return pd.DataFrame(columns=["lat", "lon", "annual_ghi_kwh_m2"])

    df = pd.DataFrame(rows)
    logger.info(
        "NASA POWER grid complete: %d/%d points returned data. "
        "Mean GHI: %.1f kWh/m²/yr (range %.1f–%.1f).",
        len(df), total_points,
        df["annual_ghi_kwh_m2"].mean(),
        df["annual_ghi_kwh_m2"].min(),
        df["annual_ghi_kwh_m2"].max(),
    )
    return df
