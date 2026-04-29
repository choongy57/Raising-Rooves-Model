"""
Building footprint segmenter for the Raising Rooves pipeline.

Queries building footprint polygons for a lat/lon bounding box using
the OpenStreetMap Overpass API — no API key, no large download required.

As a local-file alternative, the module can load building footprints from a
GeoJSON file. Two sources are supported and auto-detected by property keys:

    VicMap Feature of Interest – Buildings (recommended for Melbourne)
        Download: https://datashare.maps.vic.gov.au  (search "FOI Buildings")
        Format: GeoJSON export.  Properties used: UFI, FEATURESUBTYPE, NUM_FLOORS.
        Coverage: authoritative Victorian government data, 2–3 yr metro refresh cycle.
        License: Creative Commons Attribution 4.0

    Microsoft Australia Building Footprints (good outer-suburb fallback)
        Download: https://github.com/microsoft/AustraliaBuildingFootprints (~845 MB)
        Format: line-delimited GeoJSONL.
        License: ODbL

Usage: pass --footprint-file <path> to any CLI entry point, or set
FOOTPRINT_LOCAL_FILE in config/settings.py.

Primary source (default — no download needed):
    OpenStreetMap via Overpass API
    https://overpass-api.de/
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
from shapely import STRtree

from config.settings import DEFAULT_TILE_SIZE, DEFAULT_ZOOM
from shared.logging_config import setup_logging

logger = setup_logging("building_footprint_segmenter")

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_OVERPASS_FALLBACK_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
]
_OVERPASS_TIMEOUT = 30  # seconds
_REQUEST_TIMEOUT = 45
_REQUEST_HEADERS = {"User-Agent": "RaisingRooves/0.1 (Monash FYP)"}


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class BuildingFootprint:
    """A single building footprint polygon."""

    building_id: str                      # OSM way/relation ID, or sequential int
    area_m2: float                        # estimated area in square metres
    polygon_latlon: list[list[float]]     # [[lon, lat], ...] original coords
    polygon: list[list[int]] = field(default_factory=list)  # pixel coords on tile
    source: str = "osm"                   # "osm" or "msft"

    # OSM roof / building tags (None = not present in OSM data)
    building_type: Optional[str] = None  # e.g. "residential", "commercial", "yes"
    levels: Optional[int] = None         # building:levels tag
    roof_material: Optional[str] = None  # roof:material, e.g. "metal", "tiles"
    roof_colour: Optional[str] = None    # roof:colour, e.g. "grey", "#cc4444"
    roof_shape: Optional[str] = None     # roof:shape, e.g. "flat", "gabled", "hipped"


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
    except Exception as exc:
        logger.debug("Could not compute area for polygon: %s", exc)
        return 0.0


def _polygon_intersects_bbox(
    polygon_latlon: list[list[float]],
    south: float,
    west: float,
    north: float,
    east: float,
) -> bool:
    """Return True when a WGS84 polygon intersects the query bbox."""
    if len(polygon_latlon) < 3:
        return False
    try:
        polygon = sg.Polygon(polygon_latlon)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        return bool(polygon.intersects(sg.box(west, south, east, north)))
    except Exception as exc:
        logger.debug("Could not intersect polygon with bbox: %s", exc)
        return False


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
    Run an Overpass API query for all building ways and relations in the given bbox.

    Returns the raw JSON response dict.
    Raises RuntimeError on HTTP errors.
    """
    # Query: all ways AND relations tagged building=* within bbox.
    # Relations cover large multi-outline buildings (apartment blocks, shopping centres)
    # that are mapped as OSM multipolygons and would otherwise be silently skipped.
    query = f"""
[out:json][timeout:{_OVERPASS_TIMEOUT}];
(
  way["building"]({south},{west},{north},{east});
  relation["building"]({south},{west},{north},{east});
);
out body;
>;
out skel qt;
"""
    errors: list[str] = []
    for url in [_OVERPASS_URL, *_OVERPASS_FALLBACK_URLS]:
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    url,
                    data={"data": query},
                    timeout=_REQUEST_TIMEOUT,
                    headers=_REQUEST_HEADERS,
                )
                if r.status_code == 429:
                    wait = 10 * attempt
                    logger.warning("Overpass rate-limited at %s -- waiting %ds", url, wait)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                if url != _OVERPASS_URL:
                    logger.info("Overpass fallback succeeded via %s", url)
                return r.json()
            except requests.RequestException as exc:
                response = getattr(exc, "response", None)
                detail = ""
                if response is not None and response.text:
                    detail = f" | response: {response.text[:300].strip()}"
                errors.append(f"{url}: {exc}{detail}")
                if attempt < 3:
                    time.sleep(5 * attempt)
                    continue
                break
    if errors:
        raise RuntimeError("Overpass API failed: " + " || ".join(errors[-3:]))
    return {}


def _chain_way_node_refs(way_segs: list[list[int]]) -> list[int]:
    """
    Chain a list of OSM way node-ref sequences into a single ordered ring.

    OSM multipolygon outer rings are sometimes split across multiple ways
    that need to be stitched end-to-end by matching shared node IDs.
    """
    if not way_segs:
        return []
    result = list(way_segs[0])
    remaining = [list(s) for s in way_segs[1:]]
    while remaining:
        last_node = result[-1]
        matched = False
        for i, seg in enumerate(remaining):
            if seg[0] == last_node:
                result.extend(seg[1:])
                remaining.pop(i)
                matched = True
                break
            elif seg[-1] == last_node:
                result.extend(list(reversed(seg))[1:])
                remaining.pop(i)
                matched = True
                break
        if not matched:
            # Disconnected segment — append as-is and continue
            result.extend(remaining.pop(0))
    return result


def _osm_response_to_footprints(
    data: dict,
    tile_centre_lat: float,
    tile_centre_lon: float,
    zoom: int,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[BuildingFootprint]:
    """
    Convert an Overpass JSON response into BuildingFootprint objects.

    Handles both way elements (simple buildings) and relation elements
    (multipolygon buildings such as apartment blocks and shopping centres).
    """
    elements = data.get("elements", [])

    # Pass 1: build node and way lookups
    nodes: dict[int, tuple[float, float]] = {}
    way_node_refs: dict[int, list[int]] = {}  # way_id -> ordered node IDs
    for elem in elements:
        if elem["type"] == "node":
            nodes[elem["id"]] = (elem["lat"], elem["lon"])
        elif elem["type"] == "way":
            way_node_refs[elem["id"]] = elem.get("nodes", [])

    def _build_footprint(
        elem_id: str,
        tags: dict,
        node_refs: list[int],
    ) -> "BuildingFootprint | None":
        """Shared helper: node refs -> BuildingFootprint or None."""
        poly_latlon = []
        for node_id in node_refs:
            if node_id in nodes:
                lat, lon = nodes[node_id]
                poly_latlon.append([lon, lat])
        if len(poly_latlon) < 3:
            return None
        area = _polygon_area_m2(poly_latlon)
        if area < 10:
            return None
        pixel_poly = _project_polygon(
            poly_latlon, tile_centre_lat, tile_centre_lon, zoom, tile_size
        )
        levels_raw = tags.get("building:levels")
        try:
            levels = int(levels_raw) if levels_raw is not None else None
        except (ValueError, TypeError):
            levels = None
        return BuildingFootprint(
            building_id=elem_id,
            area_m2=round(area, 1),
            polygon_latlon=poly_latlon,
            polygon=pixel_poly,
            source="osm",
            building_type=tags.get("building") or None,
            levels=levels,
            roof_material=tags.get("roof:material") or None,
            roof_colour=tags.get("roof:colour") or None,
            roof_shape=tags.get("roof:shape") or None,
        )

    footprints: list[BuildingFootprint] = []

    # Pass 2: simple way buildings
    for elem in elements:
        if elem["type"] != "way":
            continue
        tags = elem.get("tags", {})
        if "building" not in tags:
            continue
        refs = elem.get("nodes", [])
        if len(refs) < 4:
            continue
        fp = _build_footprint(str(elem["id"]), tags, refs)
        if fp:
            footprints.append(fp)

    # Pass 3: relation buildings (multipolygon — outer member ways form the footprint)
    for elem in elements:
        if elem["type"] != "relation":
            continue
        tags = elem.get("tags", {})
        if "building" not in tags:
            continue

        outer_refs: list[list[int]] = []
        for member in elem.get("members", []):
            if member.get("role") == "outer" and member.get("type") == "way":
                way_id = member["ref"]
                refs = way_node_refs.get(way_id, [])
                if refs:
                    outer_refs.append(refs)

        if not outer_refs:
            continue

        chained = _chain_way_node_refs(outer_refs)
        fp = _build_footprint(f"r{elem['id']}", tags, chained)
        if fp:
            footprints.append(fp)
            logger.debug("Relation building r%s: %.0f m²", elem["id"], fp.area_m2)

    return footprints


# ── Local GeoJSON source (VicMap / Microsoft Building Footprints) ─────────────


def _extract_props(props: dict) -> tuple[str | None, int | None, str | None, str | None, str | None, str]:
    """
    Extract (building_type, levels, roof_material, roof_colour, roof_shape, source_label)
    from a GeoJSON feature's properties dict.

    Auto-detects VicMap (UFI key present) vs Microsoft/Overture format.
    Returns source_label "vicmap" or "msft" for the BuildingFootprint.source field.
    """
    if "UFI" in props or "ufi" in props:
        # VicMap Building Polygon
        # FEATSUBTYP = FEATURE_SUBTYPE (truncated SHP column name)
        # No floors/roof fields in this dataset
        building_type = (
            props.get("FEATSUBTYP") or props.get("FEATURESUBTYPE")
            or props.get("featuresubtype") or props.get("FTYPE") or None
        )
        return (
            building_type,
            None,   # VicMap BUILDING_POLYGON has no floor count field
            None,   # VicMap BUILDING_POLYGON has no roof material field
            None,   # VicMap BUILDING_POLYGON has no roof colour field
            None,   # VicMap BUILDING_POLYGON has no roof shape field
            "vicmap",
        )
    else:
        # Microsoft / Overture format
        levels_raw = props.get("num_floors") or props.get("levels")
        try:
            levels = int(levels_raw) if levels_raw is not None else None
        except (ValueError, TypeError):
            levels = None
        return (
            props.get("class") or props.get("building") or None,
            levels,
            props.get("roof_material") or None,
            props.get("roof_color") or None,
            props.get("roof_shape") or None,
            "msft",
        )


def _load_local_footprints(
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

    Supports VicMap FOI Buildings (GeoJSON) and Microsoft AU Building Footprints
    (line-delimited GeoJSONL). Source format is auto-detected per feature.
    Also handles plain GeoJSON FeatureCollections.

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
        for line in f:
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
                geom_type = geom.get("type")

                # Collect rings: Polygon → one ring; MultiPolygon → all outer rings
                if geom_type == "Polygon":
                    rings = [geom.get("coordinates", [[]])[0]]
                elif geom_type == "MultiPolygon":
                    rings = [poly[0] for poly in geom.get("coordinates", []) if poly]
                else:
                    continue

                props = feat.get("properties", {}) or {}
                bldg_type, levels, roof_mat, roof_col, roof_shp, src = _extract_props(props)

                # Prefer a stable feature ID over a sequential counter
                feature_id = (
                    props.get("UFI") or props.get("ufi")
                    or props.get("id") or props.get("ID")
                    or str(count)
                )

                for coords in rings:
                    if not coords or len(coords) < 3:
                        continue

                    # Keep any polygon that intersects the query area. Centroid-only
                    # filtering drops large edge-crossing buildings visible in tiles.
                    if not _polygon_intersects_bbox(coords, south, west, north, east):
                        continue

                    area = _polygon_area_m2(coords)
                    if area < 10:
                        continue

                    pixel_poly = _project_polygon(
                        coords, tile_centre_lat, tile_centre_lon, zoom, tile_size
                    )

                    footprints.append(BuildingFootprint(
                        building_id=str(feature_id),
                        area_m2=round(area, 1),
                        polygon_latlon=coords,
                        polygon=pixel_poly,
                        source=src,
                        building_type=bldg_type,
                        levels=levels,
                        roof_material=roof_mat,
                        roof_colour=roof_col,
                        roof_shape=roof_shp,
                    ))
                    count += 1

    logger.info("Loaded %d buildings from local file %s", len(footprints), local_file.name)
    return footprints


def _load_shapefile_footprints(
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
    Load building footprints from a Shapefile (.shp) and filter to bbox.

    Uses geopandas for reading; reprojects to EPSG:4326 if needed.
    Designed for VicMap Buildings (VMBUILDINGS_2D) but handles any
    polygon Shapefile with building outlines.
    """
    import geopandas as gpd

    gdf = gpd.read_file(local_file)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    # Clip to suburb bbox before iterating
    gdf = gdf.cx[west:east, south:north]
    logger.info("Shapefile bbox clip: %d features in suburb bounds", len(gdf))

    footprints: list[BuildingFootprint] = []
    count = 0

    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        # Normalise to a flat list of exterior rings (Polygon or MultiPolygon)
        from shapely.geometry import MultiPolygon, Polygon
        if isinstance(geom, Polygon):
            polys = [geom]
        elif isinstance(geom, MultiPolygon):
            polys = list(geom.geoms)
        else:
            continue

        props = {k: v for k, v in row.items() if k != "geometry"}
        bldg_type, levels, roof_mat, roof_col, roof_shp, src = _extract_props(props)

        feature_id = (
            props.get("PFI") or props.get("pfi")
            or props.get("UFI") or props.get("ufi")
            or props.get("OBJECTID") or props.get("objectid")
            or str(count)
        )

        for poly in polys:
            coords = [list(c) for c in poly.exterior.coords]  # [[lon, lat], ...]
            if len(coords) < 3:
                continue

            area = _polygon_area_m2(coords)
            if area < 10:
                continue

            pixel_poly = _project_polygon(
                coords, tile_centre_lat, tile_centre_lon, zoom, tile_size
            )

            footprints.append(BuildingFootprint(
                building_id=str(feature_id),
                area_m2=round(area, 1),
                polygon_latlon=coords,
                polygon=pixel_poly,
                source="vicmap",
                building_type=bldg_type,
                levels=levels,
                roof_material=roof_mat,
                roof_colour=roof_col,
                roof_shape=roof_shp,
            ))
            count += 1

    logger.info("Loaded %d buildings from shapefile %s", len(footprints), local_file.name)
    return footprints


# Keep old name as alias so any external callers aren't broken
_load_msft_footprints = _load_local_footprints


def _load_gpkg_footprints(
    gpkg_file: Path,
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
    Load building footprints from a GeoPackage using a fast spatial bbox query.

    Requires geopandas. The GeoPackage must have been built by
    tools/build_footprint_index.py which creates a 'buildings' layer with a
    spatial index — bbox queries return in ~0.1 s regardless of total file size.

    Args:
        gpkg_file: Path to the .gpkg file.
        south/west/north/east: Bounding box to query (EPSG:4326).
        tile_centre_lat/lon/zoom/tile_size: For pixel polygon projection.

    Returns:
        List of BuildingFootprint objects within the bbox.
    """
    import geopandas as gpd
    from shapely.geometry import MultiPolygon, Polygon

    # geopandas bbox= uses (minx, miny, maxx, maxy) = (west, south, east, north)
    gdf = gpd.read_file(str(gpkg_file), layer="buildings", bbox=(west, south, east, north))

    if gdf.empty:
        logger.info("No buildings found in GeoPackage for this bbox.")
        return []

    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs("EPSG:4326")

    footprints: list[BuildingFootprint] = []
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        if isinstance(geom, Polygon):
            rings = [list(geom.exterior.coords)]
        elif isinstance(geom, MultiPolygon):
            rings = [list(p.exterior.coords) for p in geom.geoms]
        else:
            continue

        feat_id = str(row.get("feat_id") or "") or None
        dataset = str(row.get("dataset") or "msft")
        src = "vicmap" if "vicmap" in dataset.lower() else "msft"

        for coords in rings:
            coords = [[c[0], c[1]] for c in coords]   # strip Z if present
            if len(coords) < 3:
                continue
            # The GeoPackage bbox read returns features intersecting the bbox,
            # but a MultiPolygon can contain rings outside it. Filter each ring
            # by intersection rather than centroid to keep edge buildings.
            if not _polygon_intersects_bbox(coords, south, west, north, east):
                continue
            area = _polygon_area_m2(coords)
            if area < 10:
                continue
            pixel_poly = _project_polygon(
                coords, tile_centre_lat, tile_centre_lon, zoom, tile_size
            )
            footprints.append(BuildingFootprint(
                building_id=feat_id or str(len(footprints)),
                area_m2=round(area, 1),
                polygon_latlon=coords,
                polygon=pixel_poly,
                source=src,
            ))

    logger.info("GeoPackage bbox query: %d buildings loaded from %s", len(footprints), gpkg_file.name)
    return footprints


# ── Merge helpers ─────────────────────────────────────────────────────────────


def merge_footprints(
    primary: list[BuildingFootprint],
    secondary: list[BuildingFootprint],
    iou_threshold: float = 0.3,
) -> list[BuildingFootprint]:
    """
    Merge two building footprint lists, keeping all primary buildings and adding
    secondary buildings that don't significantly overlap with any primary building.

    Overlap is measured by IoU (intersection-over-union) of the polygon areas.
    A secondary building is dropped if IoU > iou_threshold with any primary building.

    Args:
        primary: Base list (e.g. OSM). All entries are kept.
        secondary: Supplementary list (e.g. VicMap). Only non-overlapping entries added.
        iou_threshold: IoU above which two buildings are considered duplicates (default 0.3).

    Returns:
        Merged list: all primary + non-duplicate secondary buildings.
    """
    if not secondary:
        return primary
    if not primary:
        return secondary

    # Build shapely polygons for primary buildings (skip invalid)
    primary_polys: list[sg.Polygon | None] = []
    for b in primary:
        try:
            p = sg.Polygon(b.polygon_latlon)
            primary_polys.append(p if p.is_valid else p.buffer(0))
        except Exception:
            primary_polys.append(None)

    # STRtree over the valid primary polygons for O(N log N) candidate lookup
    valid_primary = [(i, p) for i, p in enumerate(primary_polys) if p is not None]
    if valid_primary:
        _, tree_polys = zip(*valid_primary)
        primary_tree = STRtree(list(tree_polys))
    else:
        primary_tree = None
        tree_polys = []

    added = 0
    merged = list(primary)
    for bldg in secondary:
        try:
            sp = sg.Polygon(bldg.polygon_latlon)
            if not sp.is_valid:
                sp = sp.buffer(0)
        except Exception:
            continue

        duplicate = False
        if primary_tree is not None:
            # query() returns indices into tree_polys (not primary_polys)
            candidates = primary_tree.query(sp)
            for idx in candidates:
                pp = tree_polys[idx]
                try:
                    intersection = pp.intersection(sp).area
                    union = pp.union(sp).area
                    if union > 0 and intersection / union > iou_threshold:
                        duplicate = True
                        break
                except Exception:
                    continue

        if not duplicate:
            merged.append(bldg)
            added += 1

    logger.info(
        "Merged footprints: %d primary + %d new from secondary (%d duplicates dropped)",
        len(primary), added, len(secondary) - added,
    )
    return merged


# ── Public API ────────────────────────────────────────────────────────────────


def query_buildings_in_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    local_file: Optional[Path] = None,
    tile_centre_lat: Optional[float] = None,
    tile_centre_lon: Optional[float] = None,
    zoom: int = DEFAULT_ZOOM,
    tile_size: int = DEFAULT_TILE_SIZE,
) -> list[BuildingFootprint]:
    """
    Return all building footprints within an arbitrary bounding box.

    Designed for suburb-level queries — one Overpass call covers the
    entire suburb rather than issuing a separate call per tile.

    Args:
        south/west/north/east: Bounding box in WGS84.
        local_file: Optional local footprint file — GeoJSON/GeoJSONL (Microsoft AU
            or VicMap) or Shapefile .shp (VicMap VMBUILDINGS_2D).
        tile_centre_lat/lon: If provided, also populate pixel polygons on
            a tile centred here. Leave None to skip pixel projection.
        zoom/tile_size: Used only when tile_centre_* are set.

    Returns:
        List of BuildingFootprint objects, deduplicated by building_id.
    """
    centre_lat = tile_centre_lat or (south + north) / 2
    centre_lon = tile_centre_lon or (west + east) / 2

    if local_file is not None:
        if not local_file.exists():
            raise FileNotFoundError(
                f"Local footprint file not found: {local_file}\n"
                "Supported sources:\n"
                "  GeoPackage index (fastest): python -m tools.build_footprint_index\n"
                "  VicMap Buildings (SHP):     https://datashare.maps.vic.gov.au\n"
                "  Microsoft AU (GeoJSONL):    https://github.com/microsoft/AustraliaBuildingFootprints"
            )
        suffix = local_file.suffix.lower()
        if suffix == ".gpkg":
            loader = _load_gpkg_footprints
        elif suffix == ".shp":
            loader = _load_shapefile_footprints
        else:
            loader = _load_local_footprints
        footprints = loader(
            local_file, south, west, north, east,
            centre_lat, centre_lon, zoom, tile_size,
        )
    else:
        logger.info(
            "OSM Overpass query for suburb bbox (%.5f,%.5f)->(%.5f,%.5f)",
            south, west, north, east,
        )
        data = _overpass_query(south, west, north, east)
        footprints = _osm_response_to_footprints(
            data, centre_lat, centre_lon, zoom, tile_size
        )

    # Deduplicate by building_id (shouldn't happen with OSM, but defensive)
    seen: set[str] = set()
    unique: list[BuildingFootprint] = []
    for f in footprints:
        if f.building_id not in seen:
            seen.add(f.building_id)
            unique.append(f)

    logger.info(
        "Found %d unique buildings in bbox | total area %.0f m2",
        len(unique), sum(b.area_m2 for b in unique),
    )
    return unique


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
    (VicMap FOI Buildings or Microsoft Australia Building Footprints).

    Args:
        centre_lat/lon: Centre coordinate of the tile.
        zoom: Tile zoom level (default 19).
        tile_size: Tile edge in pixels (default from settings).
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
                "Supported sources:\n"
                "  VicMap FOI Buildings: https://datashare.maps.vic.gov.au\n"
                "  Microsoft AU Footprints: https://github.com/microsoft/AustraliaBuildingFootprints"
            )
        loader = _load_shapefile_footprints if local_file.suffix.lower() == ".shp" else _load_local_footprints
        footprints = loader(
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
