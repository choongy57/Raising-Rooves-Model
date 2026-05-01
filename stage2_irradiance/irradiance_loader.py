"""
Irradiance data loader for the Raising Rooves pipeline.

Provides a unified interface for loading annual GHI (Global Horizontal Irradiance)
data per suburb. Source priority:

1. CSV file: manually prepared CSV with columns lat, lon, annual_ghi_kwh_m2.
   One row per BARRA2 grid cell (or any point grid). The pipeline spatially joins
   each building centroid to the nearest grid cell.

2. BARRA2 via OPeNDAP (stub — implement when NCI access is available):
   Queries the BOM BARRA2 reanalysis dataset for a bbox and time range,
   extracts av_swsfcdown (W/m²), and converts to annual kWh/m².
   Connection details are in config/settings.py (BARRA2_THREDDS_BASE, BARRA2_VARIABLES).

3. NASA POWER (free REST API, no key required):
   Samples a grid across the suburb bbox and returns annual GHI (kWh/m²/yr).
   Results are cached to data/raw/nasa_power/{suburb_key}_ghi.csv.

CSV format expected:
    lat,lon,annual_ghi_kwh_m2
    -37.80,144.96,1852.3
    -37.84,144.96,1849.1
    ...

Melbourne placeholder: ~1850 kWh/m²/yr (use MELBOURNE_DEFAULT_GHI_KWH_M2_YR from settings).
"""

import math
from pathlib import Path

import pandas as pd

from config.settings import MELBOURNE_DEFAULT_GHI_KWH_M2_YR, NASA_POWER_CACHE_DIR
from shared.logging_config import setup_logging

logger = setup_logging("irradiance_loader")

_REQUIRED_COLS = {"lat", "lon", "annual_ghi_kwh_m2"}


def load_irradiance_csv(path: Path) -> pd.DataFrame:
    """
    Load irradiance grid from a CSV file.

    Expected columns: lat, lon, annual_ghi_kwh_m2.
    Additional columns are allowed and passed through.

    Raises:
        FileNotFoundError: if path does not exist.
        ValueError: if required columns are missing.
    """
    if not path.exists():
        raise FileNotFoundError(f"Irradiance file not found: {path}")

    df = pd.read_csv(path)
    missing = _REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(
            f"Irradiance CSV is missing columns: {missing}. "
            f"Required: {_REQUIRED_COLS}. Found: {set(df.columns)}"
        )

    logger.info("Loaded %d irradiance grid cells from %s", len(df), path.name)

    max_ghi = df["annual_ghi_kwh_m2"].max()
    if max_ghi < 100:
        logger.warning(
            "annual_ghi_kwh_m2 max=%.1f looks like daily values (kWh/m²/day). "
            "Pipeline expects annual kWh/m²/yr — Melbourne is ~1650. Check your CSV units.",
            max_ghi,
        )
    elif max_ghi > 4000:
        logger.warning(
            "annual_ghi_kwh_m2 max=%.1f exceeds realistic range for Australia (max ~2500). "
            "Check your CSV units.",
            max_ghi,
        )

    return df[["lat", "lon", "annual_ghi_kwh_m2"]].copy()


def load_barra2_irradiance(
    south: float,
    west: float,
    north: float,
    east: float,
    year_start: int = 2010,
    year_end: int = 2020,
) -> pd.DataFrame:
    """
    Query BARRA2 via OPeNDAP for annual mean GHI over a bbox.

    NOT YET IMPLEMENTED — returns NotImplementedError.

    When implementing:
      - Connect to BARRA2_THREDDS_BASE (config/settings.py)
      - Variable: av_swsfcdown (W/m², instantaneous average)
      - Convert to annual kWh/m²: mean_W_m2 × 8760 / 1000
      - Return DataFrame with lat, lon, annual_ghi_kwh_m2

    NCI THREDDS access requires an NCI account and VPN/network access.
    See: https://thredds.nci.org.au/thredds/catalog/ob53/catalog.html
    """
    raise NotImplementedError(
        "BARRA2 connector not yet implemented. "
        "Provide an irradiance CSV via --irradiance-file instead. "
        "See stage2_irradiance/irradiance_loader.py for the expected CSV format."
    )


def nearest_ghi(
    building_lat: float,
    building_lon: float,
    irradiance_df: pd.DataFrame,
) -> float:
    """
    Return the annual_ghi_kwh_m2 value from the nearest grid cell to a building centroid.

    Uses Euclidean distance in degrees — accurate enough for matching buildings to a
    4km grid (BARRA2) or similar coarse irradiance data within a single suburb.
    """
    dlat = irradiance_df["lat"] - building_lat
    dlon = irradiance_df["lon"] - building_lon
    dist2 = dlat ** 2 + dlon ** 2
    idx = dist2.idxmin()
    return float(irradiance_df.loc[idx, "annual_ghi_kwh_m2"])


def make_default_irradiance_df(
    suburb_bbox: tuple[float, float, float, float],
) -> pd.DataFrame:
    """
    Return a single-row irradiance DataFrame centred on the suburb bbox.

    Uses the Melbourne default GHI constant — a placeholder for when no
    irradiance file has been provided yet.
    """
    south, west, north, east = suburb_bbox
    centre_lat = (south + north) / 2
    centre_lon = (west + east) / 2
    logger.warning(
        "No irradiance file provided — using Melbourne default GHI of %.0f kWh/m²/yr.",
        MELBOURNE_DEFAULT_GHI_KWH_M2_YR,
    )
    return pd.DataFrame([{
        "lat": centre_lat,
        "lon": centre_lon,
        "annual_ghi_kwh_m2": MELBOURNE_DEFAULT_GHI_KWH_M2_YR,
    }])


def load_nasa_power_irradiance(
    south: float,
    west: float,
    north: float,
    east: float,
    suburb_key: str,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """
    Fetch or load cached NASA POWER GHI grid for a suburb bounding box.

    On first call the function samples a grid of points across the bbox via
    the NASA POWER Climatology API and writes the result to a CSV cache file
    at ``cache_dir/{suburb_key}_ghi.csv``.  Subsequent calls read the cache
    file directly without hitting the network.

    Args:
        south:      Southern latitude of the bounding box.
        west:       Western longitude of the bounding box.
        north:      Northern latitude of the bounding box.
        east:       Eastern longitude of the bounding box.
        suburb_key: Lowercase underscore-separated suburb name used for the
                    cache filename (e.g. ``"carlton"``).
        cache_dir:  Directory to read/write the cache CSV.  Defaults to
                    ``NASA_POWER_CACHE_DIR`` from config/settings.py.

    Returns:
        DataFrame with columns: lat, lon, annual_ghi_kwh_m2.
        Empty DataFrame if the NASA POWER API is unreachable and no cache
        exists.
    """
    from stage2_irradiance.nasa_power_client import fetch_suburb_ghi_grid

    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else NASA_POWER_CACHE_DIR
    resolved_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = resolved_cache_dir / f"{suburb_key}_ghi.csv"

    if cache_path.exists():
        logger.info(
            "Loading cached NASA POWER GHI for '%s' from %s",
            suburb_key, cache_path,
        )
        df = pd.read_csv(cache_path)
        missing = {"lat", "lon", "annual_ghi_kwh_m2"} - set(df.columns)
        if missing:
            logger.warning(
                "Cached NASA POWER file %s is missing columns %s — re-fetching.",
                cache_path, missing,
            )
        else:
            logger.info(
                "Cache hit: %d grid points, mean GHI %.1f kWh/m²/yr.",
                len(df), df["annual_ghi_kwh_m2"].mean(),
            )
            return df[["lat", "lon", "annual_ghi_kwh_m2"]].copy()

    logger.info(
        "No NASA POWER cache for '%s' — fetching from API (bbox: "
        "south=%.4f west=%.4f north=%.4f east=%.4f).",
        suburb_key, south, west, north, east,
    )

    df = fetch_suburb_ghi_grid(south, west, north, east)

    if df.empty:
        logger.error(
            "NASA POWER fetch returned no data for '%s'. "
            "Cache file NOT written.",
            suburb_key,
        )
        return df

    df.to_csv(cache_path, index=False)
    logger.info(
        "NASA POWER GHI cached to %s (%d points).",
        cache_path, len(df),
    )
    return df[["lat", "lon", "annual_ghi_kwh_m2"]].copy()
