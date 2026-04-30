"""
DSM (Digital Surface Model) processor for the Raising Rooves pipeline.

Loads GeoTIFF DSM rasters, clips them to building footprint polygons, and
returns georeferenced XYZ point arrays (in metres) ready for plane-fitting.

## Recommended data sources for Melbourne (in order of preference)

1. **ELVIS — Elevation & Depth Foundation Spatial Data** (best, 1 m resolution)
   https://elevation.fsdf.org.au/
   - Select product type "1m DEM" or "Point Cloud"
   - Area: draw your suburb bounding box on the map
   - Free registration required; download GeoTIFF tiles
   - Coverage: all of metropolitan Melbourne, refreshed ~2018-2022

2. **City of Melbourne Open Data** (1 m DSM, inner suburbs only)
   https://data.melbourne.vic.gov.au/
   - Search "DSM" — available as GeoTIFF via direct download
   - Covers roughly the inner 5 km radius; good for CBD/Fitzroy/Carlton

3. **OpenTopography API** (programmatic, ~30 m COP30/SRTM fallback)
   https://portal.opentopography.org/
   - Free API key at https://portal.opentopography.org/requestAPIKey
   - Set OPENTOPO_API_KEY in .env; use download_cop30() helper below
   - 30 m resolution is sufficient to confirm gross pitch category but
     misses single-storey pitch detail on small footprints

4. **AURIN** (Australian Urban Research Infrastructure Network)
   https://data.aurin.org.au/
   - 1 m LiDAR-derived DSM for several Melbourne LGAs
   - Free with institutional login (Monash account works)

Usage (typical):
    dsm = load_dsm(Path("data/raw/dsm/suburb.tif"))
    xyz = extract_building_xyz(dsm, polygon_latlon)   # [[lon,lat], ...]
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from config.settings import RAW_DIR
from shared.logging_config import setup_logging

logger = setup_logging("dsm_processor")

DSM_DIR = RAW_DIR / "dsm"

# ── Dataclass ─────────────────────────────────────────────────────────────────


@dataclass
class DSMRaster:
    """Thin wrapper around an open rasterio dataset plus derived metadata."""

    path: Path
    crs_epsg: int           # e.g. 4326, 32755
    is_geographic: bool     # True if CRS is lat/lon (degrees), False if projected
    nodata: Optional[float]
    _dataset: object        # rasterio DatasetReader — kept open for batch reads


# ── Public API ────────────────────────────────────────────────────────────────


def load_dsm(path: Path) -> DSMRaster:
    """
    Open a GeoTIFF DSM file and return a DSMRaster handle.

    The file stays open for repeated calls to extract_building_xyz().
    Call dsm._dataset.close() (or use a context manager) when finished.

    Supports EPSG:4326 (geographic lat/lon) and any projected CRS (e.g. GDA2020
    UTM zone 55S / EPSG:7855) that rasterio can read.

    Args:
        path: Path to a single-band GeoTIFF DSM file.

    Returns:
        DSMRaster handle.

    Raises:
        FileNotFoundError: If the file does not exist.
        RuntimeError: If rasterio cannot open the file.
    """
    try:
        import rasterio
    except ImportError as exc:
        raise ImportError(
            "rasterio is required for DSM processing. "
            "Install it with: pip install rasterio"
        ) from exc

    if not path.exists():
        raise FileNotFoundError(f"DSM file not found: {path}")

    try:
        ds = rasterio.open(str(path))
    except Exception as exc:
        raise RuntimeError(f"Failed to open DSM file {path}: {exc}") from exc

    crs = ds.crs
    epsg = crs.to_epsg() if crs else None
    if epsg is None:
        logger.warning("DSM CRS has no EPSG code; assuming EPSG:4326 (geographic).")
        epsg = 4326

    is_geographic = crs.is_geographic if crs else True
    nodata = ds.nodata

    logger.info(
        "Loaded DSM: %s | CRS=EPSG:%d | size=%dx%d | nodata=%s",
        path.name, epsg, ds.width, ds.height, nodata,
    )
    return DSMRaster(path=path, crs_epsg=epsg, is_geographic=is_geographic,
                     nodata=nodata, _dataset=ds)


def extract_building_xyz(
    dsm: DSMRaster,
    polygon_latlon: list[list[float]],
    buffer_m: float = 0.0,
) -> np.ndarray:
    """
    Extract an (N, 3) array of [X_m, Y_m, Z_m] points inside a building polygon.

    X and Y are in metres relative to the polygon centroid — suitable for
    plane fitting.  Z is absolute elevation in metres from the DSM.

    Args:
        dsm: Open DSMRaster handle from load_dsm().
        polygon_latlon: Building polygon as [[lon, lat], ...] (GeoJSON order).
        buffer_m: Optional outward buffer in metres before clipping (default 0).
                  A small buffer (e.g. 0.5 m) captures eave edge pixels.

    Returns:
        Float64 ndarray of shape (N, 3). Returns empty array (shape 0, 3) if
        the polygon does not overlap the DSM or all pixels are nodata.
    """
    try:
        import rasterio
        from rasterio.mask import mask as rasterio_mask
        from shapely.geometry import Polygon, mapping
        from shapely.ops import transform as shp_transform
        import pyproj
    except ImportError as exc:
        raise ImportError(
            "rasterio, shapely, and pyproj are required. "
            "Install with: pip install rasterio shapely pyproj"
        ) from exc

    if len(polygon_latlon) < 3:
        return np.empty((0, 3), dtype=np.float64)

    # Build Shapely polygon in lon/lat (EPSG:4326)
    poly_4326 = Polygon([(c[0], c[1]) for c in polygon_latlon])

    # If DSM is in a projected CRS, reproject polygon to match
    if not dsm.is_geographic and dsm.crs_epsg != 4326:
        transformer = pyproj.Transformer.from_crs(
            "EPSG:4326", f"EPSG:{dsm.crs_epsg}", always_xy=True
        )
        poly_query = shp_transform(transformer.transform, poly_4326)
    else:
        poly_query = poly_4326

    # Apply optional buffer (convert metres to degrees for geographic CRS)
    if buffer_m > 0:
        if dsm.is_geographic:
            buf_deg = buffer_m / 111320.0
            poly_query = poly_query.buffer(buf_deg)
        else:
            poly_query = poly_query.buffer(buffer_m)

    ds = dsm._dataset

    try:
        out_image, out_transform = rasterio_mask(
            ds, [mapping(poly_query)], crop=True, nodata=np.nan, filled=True
        )
    except Exception as exc:
        logger.debug("rasterio_mask failed for polygon: %s", exc)
        return np.empty((0, 3), dtype=np.float64)

    elevation = out_image[0].astype(np.float64)

    # Apply nodata mask
    if dsm.nodata is not None:
        elevation[elevation == dsm.nodata] = np.nan

    valid_mask = ~np.isnan(elevation)
    if not valid_mask.any():
        return np.empty((0, 3), dtype=np.float64)

    rows, cols = np.where(valid_mask)
    zs = elevation[rows, cols]

    # Get geographic coords for each pixel centre
    xs_geo, ys_geo = rasterio.transform.xy(out_transform, rows, cols)
    xs_geo = np.array(xs_geo, dtype=np.float64)
    ys_geo = np.array(ys_geo, dtype=np.float64)

    # Convert to local metre coordinates relative to polygon centroid
    centroid = poly_4326.centroid
    ref_lon, ref_lat = centroid.x, centroid.y

    if dsm.is_geographic:
        # xs_geo, ys_geo are lon, lat in degrees
        cos_lat = math.cos(math.radians(ref_lat))
        x_m = (xs_geo - ref_lon) * 111320.0 * cos_lat
        y_m = (ys_geo - ref_lat) * 111320.0
    else:
        # xs_geo, ys_geo are projected metres already; just centre them
        x_m = xs_geo - xs_geo.mean()
        y_m = ys_geo - ys_geo.mean()

    return np.column_stack([x_m, y_m, zs])


def download_cop30(
    bbox: tuple[float, float, float, float],
    output_path: Path,
    api_key: str = "",
) -> Path:
    """
    Download a Copernicus DEM 30 m (COP30) tile via the OpenTopography REST API.

    This is a programmatic fallback when no high-resolution LiDAR is available.
    Resolution is ~30 m — sufficient for gross pitch categorisation.

    Args:
        bbox: (south, west, north, east) in EPSG:4326 degrees.
        output_path: Where to save the downloaded GeoTIFF.
        api_key: OpenTopography API key. If empty, reads OPENTOPO_API_KEY from .env.
                 Get a free key at https://portal.opentopography.org/requestAPIKey

    Returns:
        Path to the saved GeoTIFF.

    Raises:
        RuntimeError: On HTTP error or missing API key.
    """
    import os
    import requests
    from config.settings import PROJECT_ROOT
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    if not api_key:
        api_key = os.getenv("OPENTOPO_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OpenTopography API key required. "
            "Set OPENTOPO_API_KEY in .env or pass api_key= explicitly. "
            "Free key at https://portal.opentopography.org/requestAPIKey"
        )

    south, west, north, east = bbox
    url = (
        "https://portal.opentopography.org/API/globaldem"
        f"?demtype=COP30"
        f"&south={south}&north={north}&west={west}&east={east}"
        f"&outputFormat=GTiff"
        f"&API_Key={api_key}"
    )

    logger.info(
        "Downloading COP30 DSM for bbox (%.4f, %.4f, %.4f, %.4f)...",
        south, west, north, east,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, stream=True, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenTopography API returned {resp.status_code}: {resp.text[:200]}"
        )

    with open(output_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            fh.write(chunk)

    size_kb = output_path.stat().st_size / 1024
    logger.info("COP30 saved to %s (%.0f kB)", output_path, size_kb)
    return output_path
