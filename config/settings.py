"""
Central configuration for the Raising Rooves pipeline.

All paths, API endpoints, default parameters, and environment variable loading.
Secrets are loaded from .env via python-dotenv — never hardcoded.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ── Directory Paths ──────────────────────────────────────────────────────────

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
TILES_DIR = RAW_DIR / "tiles"
BARRA_DIR = RAW_DIR / "barra"
NASA_POWER_CACHE_DIR = RAW_DIR / "nasa_power"
PROCESSED_DIR = DATA_DIR / "processed"
MASKS_DIR = PROCESSED_DIR / "masks"
ROOF_AREAS_DIR = PROCESSED_DIR / "roof_areas"
OUTPUT_DIR = DATA_DIR / "output"
LOGS_DIR = PROJECT_ROOT / "logs"
RESEARCH_DIR = PROJECT_ROOT / "research" / "findings"

# ── API Keys (from .env) ────────────────────────────────────────────────────

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "")
CDS_API_KEY = os.getenv("CDS_API_KEY", "")

# ── Google Maps Static API ───────────────────────────────────────────────────

GOOGLE_MAPS_BASE_URL = "https://maps.googleapis.com/maps/api/staticmap"
DEFAULT_TILE_SIZE = 640  # pixels (max for free tier)
DEFAULT_ZOOM = 19  # ~0.29 m/pixel at Melbourne latitude
DEFAULT_MAP_TYPE = "satellite"

# ── BARRA2 Climate Data ─────────────────────────────────────────────────────
#
# NCI THREDDS OPeNDAP base for BARRA2.
# Path structure (confirmed from README and live catalog 2026-04-30):
#   ob53/output/reanalysis/{domain_id}/BOM/ERA5/historical/hres/BARRA-R2/v1/{freq}/{variable_id}/
# NOTE: the gdata path includes BARRA2/ but the THREDDS catalog does not.
# Domain: AUS-11 = 11 km BARRA-R2 grid (covers all of Australia).
#         AUS-04 = BARRA-C2 4 km grid (different product, limited domains).
# Variable names follow CORDEX/CF conventions — NOT UM/BOM internal names.
# av_swsfcdown and temp_scrn are the gdata variable *names inside* the file;
# the *folder/filename* uses rsds and tas respectively.
#
# NCI account required. Monash students: register at https://my.nci.org.au
# and ask your supervisor (Stuart) for project ob53 access.

BARRA2_THREDDS_BASE = "https://thredds.nci.org.au/thredds/dodsC/ob53"
BARRA2_CATALOG_BASE = "https://thredds.nci.org.au/thredds/catalog/ob53"

# BARRA2_DOMAIN: AUS-11 is the standard BARRA-R2 ~11 km grid.
BARRA2_DOMAIN = "AUS-11"

# Folder/filename identifiers on THREDDS (CORDEX/CF variable names).
# The NetCDF variable name *inside* each file may differ — see comments below.
BARRA2_VARIABLES = {
    # rsds = surface downwelling shortwave radiation flux (W/m²).
    # NetCDF variable name inside the file: rsds.
    # Replaces the old 'av_swsfcdown' which is a UM internal name not used on THREDDS.
    "solar_irradiance": "rsds",
    # rlds = surface downwelling longwave radiation flux (W/m²).
    "longwave_radiation": "rlds",
    # tas = near-surface (2 m) air temperature (K).
    # Replaces old 'temp_scrn' — same quantity, CORDEX name.
    "temperature_2m": "tas",
    # pr = precipitation flux (kg/m²/s).
    "precipitation": "pr",
}

# ── ERA5 Fallback ────────────────────────────────────────────────────────────

ERA5_VARIABLES = {
    "solar_irradiance": "ssrd",  # surface solar radiation downwards (J/m²)
    "temperature_2m": "t2m",  # 2m temperature (K)
}

# ── Melbourne Defaults ───────────────────────────────────────────────────────

MELBOURNE_CENTRE = (-37.8136, 144.9631)  # lat, lon
MELBOURNE_BBOX = (-38.1, 144.5, -37.5, 145.5)  # south, west, north, east

# ── Roof Material Priors (CSR VIC data) ──────────────────────────────────────

ROOF_MATERIAL_PRIORS = {
    "metal": 0.475,  # ~45-50% of VIC roofs
    "concrete_tile": 0.175,  # ~15-20%
    "terracotta_tile": 0.15,  # ~15%
    "other": 0.20,
}

# ── Cooling/Heating Degree Day Base Temperatures ─────────────────────────────

CDD_BASE_TEMP = 18.0  # °C — cooling needed above this
HDD_BASE_TEMP = 18.0  # °C — heating needed below this

# ── Building Footprint Supplement ────────────────────────────────────────────

# Spatially-indexed GeoPackage built once by tools/build_footprint_index.py.
# When present, Stage 1 automatically merges it with OSM (no extra flags needed).
# Build it with:  python -m tools.build_footprint_index
FOOTPRINT_SUPPLEMENT_GPKG = RAW_DIR / "footprints" / "buildings_index.gpkg"

# Fallback: raw GeoJSONL (slower — full linear scan ~23 s per suburb).
FOOTPRINT_SUPPLEMENT_GEOJSONL = RAW_DIR / "footprints" / "melbourne_overture.geojsonl"

# ── Cool Roof Physics ────────────────────────────────────────────────────────

# Solar absorptance after cool roof coating treatment (target SRI ≥ 78)
COOL_ROOF_ABSORPTANCE = 0.20

# Victorian grid emissions intensity (kg CO2-e per kWh), AEMO 2023
GRID_EMISSIONS_FACTOR_KG_KWH = 0.79

# Melbourne annual GHI fallback (kWh/m²/yr) — used when no irradiance file provided
MELBOURNE_DEFAULT_GHI_KWH_M2_YR = 1850.0

# ── Rate Limiting ────────────────────────────────────────────────────────────

TILE_DOWNLOAD_DELAY = 0.1  # seconds between API calls
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0  # exponential backoff multiplier
