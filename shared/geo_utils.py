"""
Geographic utility functions for the Raising Rooves pipeline.

Handles Mercator tile coordinate conversions, tile grid computation,
and pixel-to-real-world area calculations.

All coordinates use EPSG:4326 (WGS84 lat/lon).
"""

import math


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    """
    Convert lat/lon to Mercator tile coordinates at a given zoom level.

    Args:
        lat: Latitude in degrees.
        lon: Longitude in degrees.
        zoom: Zoom level (0-21).

    Returns:
        (tile_x, tile_y) integer tile coordinates.
    """
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_to_latlon(x: int, y: int, zoom: int) -> tuple[float, float]:
    """
    Convert Mercator tile coordinates to lat/lon (top-left corner of tile).

    Args:
        x: Tile X coordinate.
        y: Tile Y coordinate.
        zoom: Zoom level.

    Returns:
        (lat, lon) of the tile's top-left corner.
    """
    n = 2 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    lat = math.degrees(lat_rad)
    return lat, lon


def tile_centre_latlon(x: int, y: int, zoom: int) -> tuple[float, float]:
    """
    Get the lat/lon of the centre of a tile.

    Args:
        x: Tile X coordinate.
        y: Tile Y coordinate.
        zoom: Zoom level.

    Returns:
        (lat, lon) of the tile's centre.
    """
    top_left = tile_to_latlon(x, y, zoom)
    bottom_right = tile_to_latlon(x + 1, y + 1, zoom)
    lat = (top_left[0] + bottom_right[0]) / 2.0
    lon = (top_left[1] + bottom_right[1]) / 2.0
    return lat, lon


def compute_tile_grid(
    bbox: tuple[float, float, float, float], zoom: int
) -> list[tuple[int, int]]:
    """
    Compute all tile coordinates that cover a bounding box.

    Args:
        bbox: (south, west, north, east) in EPSG:4326.
        zoom: Zoom level.

    Returns:
        List of (tile_x, tile_y) tuples covering the bbox.
    """
    south, west, north, east = bbox

    # Get tile coords for corners (note: north has smaller y in Mercator)
    x_min, y_min = latlon_to_tile(north, west, zoom)  # top-left
    x_max, y_max = latlon_to_tile(south, east, zoom)  # bottom-right

    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
    return tiles


def ground_resolution(lat: float, zoom: int) -> float:
    """
    Calculate the ground resolution (metres per pixel) at a given latitude and zoom.

    Uses the Web Mercator formula:
        resolution = C * cos(lat) / 2^(zoom + 8)
    where C = Earth's circumference at equator (40075016.686 m)
    and we add 8 because tiles are 256px (Google uses 256-base tiles).

    For 640px tiles from Google Maps Static API, the effective resolution
    is the same per-pixel — the API just returns a larger image.

    Args:
        lat: Latitude in degrees.
        zoom: Zoom level.

    Returns:
        Metres per pixel.
    """
    C = 40075016.686  # Earth's circumference at equator (metres)
    return C * math.cos(math.radians(lat)) / (2 ** (zoom + 8))


def pixel_area(lat: float, zoom: int) -> float:
    """
    Calculate the real-world area of a single pixel in square metres.

    Args:
        lat: Latitude in degrees.
        zoom: Zoom level.

    Returns:
        Area of one pixel in m².
    """
    res = ground_resolution(lat, zoom)
    return res * res


def pixels_to_area_m2(pixel_count: int, lat: float, zoom: int) -> float:
    """
    Convert a pixel count from a segmentation mask to real-world area in m².

    Args:
        pixel_count: Number of pixels in the mask.
        lat: Latitude of the tile centre.
        zoom: Zoom level.

    Returns:
        Area in square metres.
    """
    return pixel_count * pixel_area(lat, zoom)
