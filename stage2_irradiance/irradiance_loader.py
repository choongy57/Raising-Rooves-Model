"""
Irradiance data loader for the Raising Rooves pipeline.

Provides a unified interface for loading annual GHI (Global Horizontal Irradiance)
data per suburb. Two paths:

1. CSV file (current): manually prepared CSV with columns lat, lon, annual_ghi_kwh_m2.
   One row per BARRA2 grid cell (or any point grid). The pipeline spatially joins
   each building centroid to the nearest grid cell.

2. BARRA2 via OPeNDAP (stub — implement when NCI access is available):
   Queries the BOM BARRA2 reanalysis dataset for a bbox and time range,
   extracts av_swsfcdown (W/m²), and converts to annual kWh/m².
   Connection details are in config/settings.py (BARRA2_THREDDS_BASE, BARRA2_VARIABLES).

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

from config.settings import MELBOURNE_DEFAULT_GHI_KWH_M2_YR
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
