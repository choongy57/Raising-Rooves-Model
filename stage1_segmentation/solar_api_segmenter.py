"""
Google Solar API segmenter for the Raising Rooves pipeline.

Replaces the Gemini Vision approach for single-building queries.
Calls buildingInsights:findClosest, which returns pre-computed roof
segment geometry (area, pitch, azimuth, bounding box) for the closest
building to a given coordinate.

Benefits over vision-model approach:
- No ML pipeline — Google has pre-computed everything from high-res imagery
- Returns actual measured area in m² (not pixel-count estimates)
- Includes roof pitch and solar azimuth — directly useful for Stage 3
- ~200ms per call vs 10+ seconds for Gemini
- Single building per call; use repeat_call_for_suburb() for suburb-wide coverage

API docs: https://developers.google.com/maps/documentation/solar/reference/rest/v1/buildingInsights/findClosest

Requires "Solar API" enabled in your Google Cloud project (same key as Maps Static API).
"""

import math
from dataclasses import dataclass, field
from typing import Optional

import requests

from config.settings import GOOGLE_MAPS_API_KEY, DEFAULT_TILE_SIZE, DEFAULT_ZOOM
from shared.logging_config import setup_logging

logger = setup_logging("solar_api_segmenter")

_SOLAR_BASE_URL = "https://solar.googleapis.com/v1/buildingInsights:findClosest"
_REQUEST_TIMEOUT = 15


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class SolarRoofSegment:
    """A single roof plane returned by the Solar API."""

    segment_id: int
    area_m2: float                        # measured area of this roof segment
    ground_area_m2: float                 # ground-projected area
    pitch_deg: float                      # roof pitch (0 = flat, 90 = vertical)
    azimuth_deg: float                    # compass orientation (0=N, 90=E, 180=S, 270=W)
    centre_lat: float
    centre_lon: float
    bbox_sw: tuple[float, float]          # (lat, lon) south-west corner
    bbox_ne: tuple[float, float]          # (lat, lon) north-east corner
    sunshine_hours_per_year: float        # median annual sunshine hours
    # Pixel polygon on a tile image (populated by latlon_bbox_to_pixels)
    polygon: list[list[int]] = field(default_factory=list)
    pixel_count: int = 0


@dataclass
class SolarBuildingResult:
    """Full Solar API result for one building."""

    lat: float
    lon: float
    building_bbox_sw: tuple[float, float]
    building_bbox_ne: tuple[float, float]
    whole_roof_area_m2: float
    whole_roof_ground_area_m2: float
    max_sunshine_hours_per_year: float
    imagery_date: str                     # "YYYY-MM-DD"
    imagery_quality: str
    segments: list[SolarRoofSegment] = field(default_factory=list)


# ── Coordinate helpers ────────────────────────────────────────────────────────


def _latlon_to_pixel(
    lat: float,
    lon: float,
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> tuple[int, int]:
    """
    Convert a lat/lon to pixel coordinates on a tile centred at tile_centre_*.

    Uses the Web Mercator projection formula.
    Returns (px_x, px_y) clamped to [0, tile_size-1].
    """
    C = 40075016.686  # Earth circumference at equator (m)
    metres_per_px = C * math.cos(math.radians(tile_centre_lat)) / (2 ** (zoom + 8))

    # Delta in metres (approx, valid for small offsets)
    dlat_m = (lat - tile_centre_lat) * (math.pi / 180) * 6371000
    dlon_m = (lon - tile_centre_lon) * (math.pi / 180) * 6371000 * math.cos(math.radians(tile_centre_lat))

    cx = tile_size // 2
    cy = tile_size // 2

    px = cx + int(dlon_m / metres_per_px)
    py = cy - int(dlat_m / metres_per_px)   # y axis inverted in image coords

    px = max(0, min(tile_size - 1, px))
    py = max(0, min(tile_size - 1, py))
    return px, py


def _bbox_to_polygon(
    sw: tuple[float, float],
    ne: tuple[float, float],
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[list[int]]:
    """
    Convert a lat/lon bounding box (sw, ne) into a 4-vertex pixel polygon
    on the tile.

    Returns [[x,y], ...] clockwise from top-left.
    """
    sw_lat, sw_lon = sw
    ne_lat, ne_lon = ne

    # Four corners: NW, NE, SE, SW
    nw = _latlon_to_pixel(ne_lat, sw_lon, tile_centre_lat, tile_centre_lon, zoom, tile_size)
    ne_px = _latlon_to_pixel(ne_lat, ne_lon, tile_centre_lat, tile_centre_lon, zoom, tile_size)
    se = _latlon_to_pixel(sw_lat, ne_lon, tile_centre_lat, tile_centre_lon, zoom, tile_size)
    sw_px = _latlon_to_pixel(sw_lat, sw_lon, tile_centre_lat, tile_centre_lon, zoom, tile_size)

    return [list(nw), list(ne_px), list(se), list(sw_px)]


def _bbox_pixel_count(polygon: list[list[int]]) -> int:
    """Approximate pixel count for a 4-vertex convex polygon (shoelace formula)."""
    n = len(polygon)
    area = 0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]
    return max(0, abs(area) // 2)


# ── API call ──────────────────────────────────────────────────────────────────


def get_building_insights(lat: float, lon: float) -> dict:
    """
    Call buildingInsights:findClosest and return the raw JSON response dict.

    Raises:
        RuntimeError: on non-200 HTTP response (includes helpful error message).
        requests.RequestException: on network failure.
    """
    params = {
        "location.latitude": lat,
        "location.longitude": lon,
        "key": GOOGLE_MAPS_API_KEY,
    }
    r = requests.get(_SOLAR_BASE_URL, params=params, timeout=_REQUEST_TIMEOUT)
    if r.status_code == 403:
        data = r.json()
        msg = data.get("error", {}).get("message", r.text)
        if "SERVICE_DISABLED" in msg or "not been used" in msg:
            raise RuntimeError(
                "Solar API is not enabled on your Google Cloud project.\n"
                "Enable it at: https://console.cloud.google.com/apis/api/solar.googleapis.com\n"
                f"(Project ID embedded in error: {msg})"
            )
        raise RuntimeError(f"Solar API 403: {msg}")
    if r.status_code == 404:
        raise RuntimeError(
            f"No building found near ({lat}, {lon}). "
            "The Solar API may not have coverage for this location."
        )
    if not r.ok:
        raise RuntimeError(f"Solar API {r.status_code}: {r.text[:300]}")
    return r.json()


def parse_building_result(
    data: dict,
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int = DEFAULT_ZOOM,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> SolarBuildingResult:
    """
    Parse a raw buildingInsights API response into a SolarBuildingResult.

    Also populates pixel polygons for each roof segment (useful for tile
    annotation).

    Args:
        data: Raw JSON dict from get_building_insights().
        tile_centre_lat/lon: Centre of the satellite tile (for pixel projection).
        zoom: Tile zoom level.
        tile_size: Tile pixel size.

    Returns:
        SolarBuildingResult with populated segments.
    """
    centre = data.get("center", {})
    bbox = data.get("boundingBox", {})
    sw = bbox.get("sw", {})
    ne = bbox.get("ne", {})

    solar = data.get("solarPotential", {})
    whole = solar.get("wholeRoofStats", {})

    # Imagery date
    idate = data.get("imageryDate", {})
    date_str = f"{idate.get('year', '?')}-{idate.get('month', '?'):02d}-{idate.get('day', '?'):02d}" \
        if isinstance(idate.get("month"), int) else "unknown"

    result = SolarBuildingResult(
        lat=centre.get("latitude", tile_centre_lat),
        lon=centre.get("longitude", tile_centre_lon),
        building_bbox_sw=(sw.get("latitude", 0), sw.get("longitude", 0)),
        building_bbox_ne=(ne.get("latitude", 0), ne.get("longitude", 0)),
        whole_roof_area_m2=whole.get("areaMeters2", 0.0),
        whole_roof_ground_area_m2=whole.get("groundAreaMeters2", 0.0),
        max_sunshine_hours_per_year=solar.get("maxSunshineHoursPerYear", 0.0),
        imagery_date=date_str,
        imagery_quality=data.get("imageryQuality", "unknown"),
    )

    for i, seg in enumerate(solar.get("roofSegmentStats", [])):
        stats = seg.get("stats", {})
        seg_centre = seg.get("center", {})
        seg_bbox = seg.get("boundingBox", {})
        seg_sw = seg_bbox.get("sw", {})
        seg_ne = seg_bbox.get("ne", {})

        sw_pair = (seg_sw.get("latitude", 0), seg_sw.get("longitude", 0))
        ne_pair = (seg_ne.get("latitude", 0), seg_ne.get("longitude", 0))

        # Sunshine: median value from quantiles (index 5 of 11 = median)
        sunshine_q = stats.get("sunshineQuantiles", [])
        sunshine_median = sunshine_q[5] if len(sunshine_q) > 5 else 0.0

        polygon = _bbox_to_polygon(
            sw_pair, ne_pair,
            tile_centre_lat, tile_centre_lon,
            zoom, tile_size,
        )
        pixel_count = _bbox_pixel_count(polygon)

        result.segments.append(SolarRoofSegment(
            segment_id=i,
            area_m2=stats.get("areaMeters2", 0.0),
            ground_area_m2=stats.get("groundAreaMeters2", 0.0),
            pitch_deg=seg.get("pitchDegrees", 0.0),
            azimuth_deg=seg.get("azimuthDegrees", 0.0),
            centre_lat=seg_centre.get("latitude", 0.0),
            centre_lon=seg_centre.get("longitude", 0.0),
            bbox_sw=sw_pair,
            bbox_ne=ne_pair,
            sunshine_hours_per_year=sunshine_median,
            polygon=polygon,
            pixel_count=pixel_count,
        ))

    logger.info(
        "Solar API: building at (%.5f, %.5f) | %.0f m2 total roof | %d segments | imagery %s (%s)",
        result.lat, result.lon,
        result.whole_roof_area_m2,
        len(result.segments),
        result.imagery_date,
        result.imagery_quality,
    )
    return result


def segment_building(
    lat: float,
    lon: float,
    tile_centre_lat: Optional[float] = None,
    tile_centre_lon: Optional[float] = None,
    zoom: int = DEFAULT_ZOOM,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> SolarBuildingResult:
    """
    High-level: fetch + parse Solar API data for the building nearest (lat, lon).

    Args:
        lat, lon: Query coordinate (will find nearest building).
        tile_centre_lat/lon: Used for pixel polygon projection. Defaults to lat/lon.
        zoom: Tile zoom level.
        tile_size: Tile pixel size.

    Returns:
        SolarBuildingResult with roof segments populated.
    """
    data = get_building_insights(lat, lon)
    return parse_building_result(
        data,
        tile_centre_lat=tile_centre_lat if tile_centre_lat is not None else lat,
        tile_centre_lon=tile_centre_lon if tile_centre_lon is not None else lon,
        zoom=zoom,
        tile_size=tile_size,
    )
