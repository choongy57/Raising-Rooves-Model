"""Tests for stage1_segmentation/area_estimator.py — area calculations."""

import pytest

from stage1_segmentation.area_estimator import (
    RoofArea,
    SuburbAreaSummary,
    aggregate_suburb_areas,
    estimate_roof_area,
    roof_areas_to_dataframe,
)

MELBOURNE_LAT = -37.8136
ZOOM_19 = 19


class TestEstimateRoofArea:
    def test_positive_pixels(self):
        area = estimate_roof_area(1000, MELBOURNE_LAT, ZOOM_19)
        assert area > 0

    def test_zero_pixels(self):
        area = estimate_roof_area(0, MELBOURNE_LAT, ZOOM_19)
        assert area == 0.0

    def test_scales_linearly(self):
        area_1000 = estimate_roof_area(1000, MELBOURNE_LAT, ZOOM_19)
        area_2000 = estimate_roof_area(2000, MELBOURNE_LAT, ZOOM_19)
        assert abs(area_2000 / area_1000 - 2.0) < 0.01


class TestAggregateSuburbAreas:
    def test_empty_list(self):
        summary = aggregate_suburb_areas([], "TestSuburb")
        assert summary.num_roofs == 0
        assert summary.total_roof_area_m2 == 0.0

    def test_single_roof(self):
        roofs = [
            RoofArea(
                roof_id="test_1",
                area_m2=150.0,
                pixel_count=1800,
                tile_x=1,
                tile_y=1,
                lat=-37.8,
                lon=144.9,
                material="metal_light",
                colour="white",
                confidence=0.8,
            )
        ]
        summary = aggregate_suburb_areas(roofs, "TestSuburb")
        assert summary.num_roofs == 1
        assert summary.total_roof_area_m2 == 150.0
        assert summary.mean_roof_area_m2 == 150.0
        assert "metal_light" in summary.material_breakdown

    def test_multiple_roofs(self):
        roofs = [
            RoofArea("r1", 100.0, 1200, 1, 1, -37.8, 144.9, "metal_light", "white", 0.8),
            RoofArea("r2", 200.0, 2400, 1, 1, -37.8, 144.9, "terracotta", "red", 0.7),
            RoofArea("r3", 150.0, 1800, 1, 1, -37.8, 144.9, "metal_light", "grey", 0.6),
        ]
        summary = aggregate_suburb_areas(roofs, "TestSuburb")
        assert summary.num_roofs == 3
        assert summary.total_roof_area_m2 == 450.0
        assert summary.mean_roof_area_m2 == 150.0
        assert summary.material_breakdown["metal_light"] == 250.0
        assert summary.material_breakdown["terracotta"] == 200.0


class TestRoofAreasToDataframe:
    def test_empty(self):
        df = roof_areas_to_dataframe([], "Test")
        assert len(df) == 0
        assert "suburb" in df.columns

    def test_has_required_columns(self):
        roofs = [
            RoofArea("r1", 100.0, 1200, 1, 1, -37.8, 144.9, "metal", "white", 0.8)
        ]
        df = roof_areas_to_dataframe(roofs, "Richmond")
        required = ["suburb", "roof_id", "area_m2", "material", "colour", "lat", "lon"]
        for col in required:
            assert col in df.columns
        assert df.iloc[0]["suburb"] == "Richmond"
