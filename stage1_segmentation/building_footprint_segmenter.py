"""
Building footprint segmenter for the Raising Rooves pipeline.

Queries building footprint polygons for a lat/lon bounding box using
the OpenStreetMap Overpass API — no API key, no large download required.

As a local-file alternative, the module can also load building footprints
from a GeoJSON file (e.g. the Microsoft Australia Building Footprints dataset,
downloaded from https://github.com/microsoft/AustraliaBuildingFootprints).
The Microsoft dataset is ~845 MB zipped and covers all of Australia including
Melbourne. To use it: download, unzip, and set FOOTPRINT_LOCAL_FILE in
config/settings.py (or pass local_file= to query_buildings_in_tile).

Primary source (default — no download needed):
    OpenStreetMap via Overpass API
    https://overpass-api.de/
    License: ODbL

Alternative local source:
    Microsoft Australia Building Footprints
    https://github.com/microsoft/AustraliaBuildingFootprints
    License: ODbL
"""

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
import shapely.geometry as sg

from config.settings import DEFAULT_TILE_SIZE, DEFAULT_ZOOM
from shared.logging_config import setup_logging

logger = setup_logging("building_footprint_segmenter")

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_TIMEOUT = 30  # seconds
_REQUEST_TIMEOUT = 45


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class BuildingFootprint:
    """A single building footprint polygon."""

    building_id: str                      # OSM way/relation ID, or sequential int
    area_m2: float                        # estimated area in square metres
    polygon_latlon: list[list[float]]     # [[lon, lat], ...] original coords
    polygon: list[list[int]] = field(default_factory=list)  # pixel coords on tile
    source: str = "osm"                   # "osm" or "msft"


@dataclass
class FootprintQueryResult:
    """All building footprints found in the query area."""

    query_lat: float
    query_lon: float
    tile_bbox: tuple[float, float, float, float]  # (south, west, north, east)
    buildings: list[BuildingFootprint] = field(default_factory=list)

    @property
    def total_area_m2(self) -> float:
        return sum(b.area_m2 for b in self.buildings)

    @property
    def count(self) -> int:
        return len(self.buildings)


# ── Coordinate helpers ────────────────────────────────────────────────────────


def _tile_bbox(
    centre_lat: float,
    centre_lon: float,
    zoom: int = DEFAULT_ZOOM,
    tile_size: int = DEFAULT_TILE_SIZE,
    pad_factor: float = 1.1,
) -> tuple[float, float, float, float]:
    """
    Compute the (south, west, north, east) bounding box of a tile.

    Args:
        centre_lat/lon: Tile centre in WGS84.
        zoom: Tile zoom level.
        tile_size: Tile edge length in pixels.
        pad_factor: Expand bbox by this factor to catch edge buildings.

    Returns:
        (south, west, north, east) in decimal degrees.
    """
    C = 40075016.686  # Earth circumference (m)
    metres_per_px = C * math.cos(math.radians(centre_lat)) / (2 ** (zoom + 8))
    half_m = (tile_size / 2) * metres_per_px * pad_factor

    dlat = half_m / 111320.0
    dlon = half_m / (111320.0 * math.cos(math.radians(centre_lat)))

    return (
        centre_lat - dlat,
        centre_lon - dlon,
        centre_lat + dlat,
        centre_lon + dlon,
    )


def _latlon_to_pixel(
    lat: float,
    lon: float,
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> tuple[int, int]:
    """Convert a lat/lon to pixel (x, y) on a tile centred at tile_centre_*."""
    C = 40075016.686
    metres_per_px = C * math.cos(math.radians(tile_centre_lat)) / (2 ** (zoom + 8))

    dlat_m = (lat - tile_centre_lat) * (math.pi / 180) * 6371000
    dlon_m = (lon - tile_centre_lon) * (math.pi / 180) * 6371000 * math.cos(math.radians(tile_centre_lat))

    cx, cy = tile_size // 2, tile_size // 2
    px = cx + int(dlon_m / metres_per_px)
    py = cy - int(dlat_m / metres_per_px)

    return max(0, min(tile_size - 1, px)), max(0, min(tile_size - 1, py))


def _polygon_area_m2(polygon_latlon: list[list[float]]) -> float:
    """Compute approximate area of a lat/lon polygon in square metres (Shoelace)."""
    if len(polygon_latlon) < 3:
        return 0.0
    try:
        geom = sg.Polygon(polygon_latlon)
        # Rough conversion: 1 degree lat ≈ 111320 m; adjust lon for latitude
        centroid_lat = geom.centroid.y
        lat_scale = 111320.0
        lon_scale = 111320.0 * math.cos(math.radians(centroid_lat))
        # Scale x (lon) and y (lat) independently then compute area
        scaled_pts = [(x * lon_scale, y * lat_scale) for x, y in geom.exterior.coords]
        return abs(sg.Polygon(scaled_pts).area)
    except Exception:
        return 0.0


def _project_polygon(
    polygon_latlon: list[list[float]],
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[list[int]]:
    """Project a list of [lon, lat] coords to pixel [x, y] coords on a tile."""
    return [
        list(_latlon_to_pixel(lat, lon, tile_centre_lat, tile_centre_lon, zoom, tile_size))
        for lon, lat in polygon_latlon
    ]


# ── OSM Overpass query ────────────────────────────────────────────────────────


def _overpass_query(south: float, west: float, north: float, east: float) -> dict:
    """
    Run an Overpass API query for all building ways in the given bbox.

    Returns the raw JSON response dict.
    Raises RuntimeError on HTTP errors.
    """
    # Query: all ways and relations tagged building=* within bbox
    query = f"""
[out:json][timeout:{_OVERPASS_TIMEOUT}];
(
  way["building"]({south},{west},{north},{east});
);
out body;
>;
out skel qt;
"""
    for attempt in range(1, 4):
        try:
            r = requests.post(
                _OVERPASS_URL,
                data={"data": query},
                timeout=_REQUEST_TIMEOUT,
            )
            if r.status_code == 429:
                wait = 10 * attempt
                logger.warning("Overpass rate-limited -- waiting %ds", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            if attempt == 3:
                raise RuntimeError(f"Overpass API failed after 3 attempts: {exc}") from exc
            time.sleep(5 * attempt)
    return {}


def _osm_response_to_footprints(
    data: dict,
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[BuildingFootprint]:
    """
    Convert an Overpass JSON response into BuildingFootprint objects.

    The response contains 'nodes' (id -> lat/lon) and 'ways' (id -> node refs).
    We reconstruct each way's polygon from its nodes.
    """
    # Build node lookup: id -> (lat, lon)
    nodes: dict[int, tuple[float, float]] = {}
    for elem in data.get("elements", []):
        if elem["type"] == "node":
            nodes[elem["id"]] = (elem["lat"], elem["lon"])

    footprints: list[BuildingFootprint] = []
    for elem in data.get("elements", []):
        if elem["type"] != "way":
            continue
        if "building" not in elem.get("tags", {}):
            continue

        refs = elem.get("nodes", [])
        if len(refs) < 4:
            continue

        # Reconstruct polygon as [lon, lat] pairs (GeoJSON order)
        poly_latlon = []
        for node_id in refs:
            if node_id in nodes:
                lat, lon = nodes[node_id]
                poly_latlon.append([lon, lat])

        if len(poly_latlon) < 3:
            continue

        area = _polygon_area_m2(poly_latlon)
        if area < 10:  # discard tiny slivers (< 10 m2)
            continue

        pixel_poly = _project_polygon(
            poly_latlon, tile_centre_lat, tile_centre_lon, zoom, tile_size
        )

        footprints.append(BuildingFootprint(
            building_id=str(elem["id"]),
            area_m2=round(area, 1),
            polygon_latlon=poly_latlon,
            polygon=pixel_poly,
            source="osm",
        ))

    return footprints


# ── Local GeoJSON source (Microsoft Building Footprints) ─────────────────────


def _load_msft_footprints(
    local_file: Path,
    south: float,
    west: float,
    north: float,
    east: float,
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[BuildingFootprint]:
    """
    Load building footprints from a local GeoJSON file and filter to bbox.

    Supports both GeoJSON FeatureCollection and line-delimited GeoJSONL.

    Args:
        local_file: Path to the downloaded GeoJSON/GeoJSONL file.
        south/west/north/east: Bounding box to filter to.
        tile_centre_lat/lon/zoom/tile_size: For pixel projection.

    Returns:
        List of BuildingFootprint objects within the bbox.
    """
    footprints: list[BuildingFootprint] = []
    count = 0

    with open(local_file, encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Handle both standalone features and FeatureCollection
            if feature.get("type") == "FeatureCollection":
                features = feature.get("features", [])
            elif feature.get("type") == "Feature":
                features = [feature]
            else:
                continue

            for feat in features:
                geom = feat.get("geometry", {})
                if geom.get("type") != "Polygon":
                    continue
                coords = geom.get("coordinates", [[]])[0]  # outer ring
                if not coords or len(coords) < 3:
                    continue

                # Quick bbox check on centroid
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                c_lon = sum(lons) / len(lons)
                c_lat = sum(lats) / len(lats)
                if not (south <= c_lat <= north and west <= c_lon <= east):
                    continue

                area = _polygon_area_m2(coords)
                if area < 10:
                    continue

                pixel_poly = _project_polygon(
                    coords, tile_centre_lat, tile_centre_lon, zoom, tile_size
                )
                footprints.append(BuildingFootprint(
                    building_id=str(count),
                    area_m2=round(area, 1),
                    polygon_latlon=coords,
                    polygon=pixel_poly,
                    source="msft",
                ))
                count += 1

    logger.info("Loaded %d buildings from local file %s", len(footprints), local_file.name)
    return footprints


# ── Public API ────────────────────────────────────────────────────────────────


def query_buildings_in_tile(
    centre_lat: float,
    centre_lon: float,
    zoom: int = DEFAULT_ZOOM,
    tile_size: int = DEFAULT_TILE_SIZE,
    local_file: Optional[Path] = None,
) -> FootprintQueryResult:
    """
    Return all building footprints covering a satellite tile centred at (lat, lon).

    By default, queries the OSM Overpass API (no key, no download required).
    If local_file is provided, reads from that GeoJSON/GeoJSONL file instead
    (e.g. the Microsoft Australia Building Footprints dataset).

    Args:
        centre_lat/lon: Centre coordinate of the tile.
        zoom: Tile zoom level (default 19).
        tile_size: Tile edge in pixels (default 640).
        local_file: Optional path to local GeoJSON footprints file.

    Returns:
        FootprintQueryResult with all buildings found in the tile area.
    """
    bbox = _tile_bbox(centre_lat, centre_lon, zoom, tile_size)
    south, west, north, east = bbox

    if local_file is not None:
        if not local_file.exists():
            raise FileNotFoundError(
                f"Local footprint file not found: {local_file}\n"
                "Download the Microsoft Australia Building Footprints from:\n"
                "  https://github.com/microsoft/AustraliaBuildingFootprints"
            )
        footprints = _load_msft_footprints(
            local_file, south, west, north, east,
            centre_lat, centre_lon, zoom, tile_size,
        )
        source_label = f"local file {local_file.name}"
    else:
        logger.info(
            "Querying OSM Overpass for buildings in bbox "
            "(%.5f, %.5f) -> (%.5f, %.5f)",
            south, west, north, east,
        )
        data = _overpass_query(south, west, north, east)
        footprints = _osm_response_to_footprints(
            data, centre_lat, centre_lon, zoom, tile_size
        )
        source_label = "OSM Overpass API"

    logger.info(
        "Found %d buildings via %s | total area %.0f m2",
        len(footprints), source_label, sum(b.area_m2 for b in footprints),
    )

    return FootprintQueryResult(
        query_lat=centre_lat,
        query_lon=centre_lon,
        tile_bbox=bbox,
        buildings=footprints,
    )
