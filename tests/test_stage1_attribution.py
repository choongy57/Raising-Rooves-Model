"""
Tests for the new Stage 1 attribution columns:
  orientation_deg, roof_surface_area_m2, is_flat, pitch_source, pitch_basis.
"""

import math

import pytest

from config.settings import FLAT_PITCH_THRESHOLD_DEG
from stage1_segmentation.pipeline import (
    _orientation_from_polygon,
    _assumed_pitch_deg,
    _building_to_row,
)
from stage1_segmentation.building_footprint_segmenter import BuildingFootprint


# ── _orientation_from_polygon ────────────────────────────────────────────────


def test_orientation_north_facing_wall():
    # Rectangle with longest wall running east–west (lat constant).
    # Longest edge bearing ≈ 90° (east). Normal = 90+90 = 180° (south).
    poly = [
        [144.96, -37.80],
        [144.97, -37.80],  # long east–west edge
        [144.97, -37.801],
        [144.96, -37.801],
        [144.96, -37.80],
    ]
    result = _orientation_from_polygon(poly)
    assert result is not None
    # Longest edge is east–west; its bearing ≈ 90° (east direction).
    # Normal should be ≈ 180° (south) or ≈ 0° (north) depending on sign.
    # Either 180 ± 45 is acceptable.
    assert 135 <= result <= 225 or result <= 45 or result >= 315


def test_orientation_returns_none_for_degenerate_polygon():
    assert _orientation_from_polygon([]) is None
    assert _orientation_from_polygon([[144.96, -37.80]]) is None


def test_orientation_in_range():
    poly = [
        [144.96, -37.80],
        [144.965, -37.80],
        [144.965, -37.805],
        [144.96, -37.805],
        [144.96, -37.80],
    ]
    result = _orientation_from_polygon(poly)
    assert result is not None
    assert 0.0 <= result < 360.0


# ── _building_to_row new columns ─────────────────────────────────────────────


def _make_building(roof_shape=None, building_type="residential", levels=None):
    return BuildingFootprint(
        building_id="test_1",
        area_m2=100.0,
        polygon_latlon=[
            [144.96, -37.80],
            [144.961, -37.80],
            [144.961, -37.801],
            [144.96, -37.801],
            [144.96, -37.80],
        ],
        source="osm",
        building_type=building_type,
        levels=levels,
        roof_shape=roof_shape,
    )


def test_row_has_required_columns():
    bldg = _make_building()
    row = _building_to_row(bldg, "Carlton", 0)
    for col in [
        "orientation_deg", "roof_surface_area_m2", "is_flat",
        "pitch_source", "pitch_basis", "absorptance_estimate", "absorptance_uncertainty",
    ]:
        assert col in row, f"Missing column: {col}"


def test_pitch_source_is_assumed():
    row = _building_to_row(_make_building(), "Carlton", 0)
    assert row["pitch_source"] == "assumed"


def test_pitch_basis_reflects_roof_shape_tag():
    row = _building_to_row(_make_building(roof_shape="gabled"), "Carlton", 0)
    assert row["pitch_basis"] == "roof_shape:gabled"


def test_pitch_basis_reflects_multistorey_override():
    row = _building_to_row(_make_building(levels=6), "Carlton", 0)
    assert row["pitch_basis"] == "levels>=4"


def test_pitch_basis_reflects_building_type():
    row = _building_to_row(_make_building(building_type="church"), "Carlton", 0)
    assert row["pitch_basis"] == "building_type:church"


def test_pitch_basis_falls_back_to_residential_default():
    row = _building_to_row(
        _make_building(roof_shape=None, building_type="residential", levels=None),
        "Carlton", 0,
    )
    assert row["pitch_basis"] == "residential_default"


def test_is_flat_true_for_flat_roof():
    row = _building_to_row(_make_building(roof_shape="flat"), "Carlton", 0)
    assert row["is_flat"] is True
    assert row["pitch_deg"] == 0.0


def test_is_flat_false_for_typical_pitch():
    row = _building_to_row(_make_building(roof_shape="gabled"), "Carlton", 0)
    assert row["is_flat"] is False
    assert row["pitch_deg"] >= FLAT_PITCH_THRESHOLD_DEG


def test_roof_surface_area_larger_than_footprint_for_pitched_roof():
    bldg = _make_building(roof_shape="gabled")
    row = _building_to_row(bldg, "Carlton", 0)
    assert row["roof_surface_area_m2"] > row["area_m2"]


def test_roof_surface_area_equals_footprint_for_flat_roof():
    bldg = _make_building(roof_shape="flat")
    row = _building_to_row(bldg, "Carlton", 0)
    assert row["roof_surface_area_m2"] == row["area_m2"]


def test_multistorey_building_is_flat():
    bldg = _make_building(levels=5)
    row = _building_to_row(bldg, "Carlton", 0)
    assert row["is_flat"] is True


def test_orientation_deg_is_populated():
    bldg = _make_building()
    row = _building_to_row(bldg, "Carlton", 0)
    assert row["orientation_deg"] is not None
    assert 0.0 <= row["orientation_deg"] < 360.0
