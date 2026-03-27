"""Tests for shared/geo_utils.py — Mercator tile math and area calculations."""

import math

import pytest

from shared.geo_utils import (
    compute_tile_grid,
    ground_resolution,
    latlon_to_tile,
    pixel_area,
    pixels_to_area_m2,
    tile_centre_latlon,
    tile_to_latlon,
)

# Melbourne reference coordinates
MELBOURNE_LAT = -37.8136
MELBOURNE_LON = 144.9631
ZOOM_19 = 19


class TestLatLonToTile:
    def test_melbourne_zoom_19(self):
        x, y = latlon_to_tile(MELBOURNE_LAT, MELBOURNE_LON, ZOOM_19)
        assert isinstance(x, int)
        assert isinstance(y, int)
        # At zoom 19, Melbourne tiles should be large numbers
        assert x > 400000
        assert y > 200000

    def test_equator_prime_meridian(self):
        x, y = latlon_to_tile(0.0, 0.0, 0)
        assert x == 0
        assert y == 0


class TestTileToLatLon:
    def test_round_trip(self):
        """Converting lat/lon → tile → lat/lon should land in the same tile."""
        x, y = latlon_to_tile(MELBOURNE_LAT, MELBOURNE_LON, ZOOM_19)
        lat, lon = tile_to_latlon(x, y, ZOOM_19)
        # The returned lat/lon is the top-left corner of the tile
        # Re-converting should give the same tile coords
        x2, y2 = latlon_to_tile(lat, lon, ZOOM_19)
        assert x == x2
        assert y == y2


class TestGroundResolution:
    def test_melbourne_zoom_19(self):
        res = ground_resolution(MELBOURNE_LAT, ZOOM_19)
        # At zoom 19, Melbourne: ~0.29 m/pixel
        assert 0.2 < res < 0.4

    def test_equator_higher_resolution(self):
        """Resolution should be higher at the equator (Mercator distortion)."""
        res_equator = ground_resolution(0.0, ZOOM_19)
        res_melbourne = ground_resolution(MELBOURNE_LAT, ZOOM_19)
        assert res_equator > res_melbourne  # More metres per pixel at equator

    def test_higher_zoom_means_higher_resolution(self):
        res_18 = ground_resolution(MELBOURNE_LAT, 18)
        res_19 = ground_resolution(MELBOURNE_LAT, 19)
        # Higher zoom = smaller ground resolution (more detail)
        assert res_19 < res_18
        assert abs(res_18 / res_19 - 2.0) < 0.01  # Should be exactly 2x


class TestPixelArea:
    def test_melbourne_zoom_19(self):
        area = pixel_area(MELBOURNE_LAT, ZOOM_19)
        # ~0.29 m/pixel → ~0.084 m² per pixel
        assert 0.04 < area < 0.15

    def test_consistent_with_ground_resolution(self):
        res = ground_resolution(MELBOURNE_LAT, ZOOM_19)
        area = pixel_area(MELBOURNE_LAT, ZOOM_19)
        assert abs(area - res * res) < 1e-10


class TestPixelsToAreaM2:
    def test_known_roof_size(self):
        """A typical Melbourne house roof is ~150-200 m²."""
        # At zoom 19, that's roughly 150/0.084 ≈ 1786 pixels
        area = pixels_to_area_m2(1800, MELBOURNE_LAT, ZOOM_19)
        assert 100 < area < 300  # Should be in the ballpark of a house roof


class TestComputeTileGrid:
    def test_richmond_bbox(self):
        bbox = (-37.8300, 144.9850, -37.8050, 145.0150)
        tiles = compute_tile_grid(bbox, ZOOM_19)
        assert len(tiles) > 0
        # Should cover a reasonable number of tiles for a suburb
        assert len(tiles) < 10000  # Sanity check: not too many

    def test_single_point(self):
        """A very small bbox should produce at least 1 tile."""
        bbox = (-37.8137, 144.9630, -37.8135, 144.9632)
        tiles = compute_tile_grid(bbox, ZOOM_19)
        assert len(tiles) >= 1
