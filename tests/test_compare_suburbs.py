"""
Tests for tools/compare_suburbs.py.

Uses a tiny synthetic Stage 3 parquet in a temp OUTPUT_DIR so no real pipeline
outputs are required.
"""

import math
import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

import tools.compare_suburbs as cs


def _make_stage3_df(n: int = 100) -> pd.DataFrame:
    return pd.DataFrame({
        "building_id": [f"b{i}" for i in range(n)],
        "lat": [-37.80 + i * 0.0001 for i in range(n)],
        "lon": [144.96 + i * 0.0001 for i in range(n)],
        "area_m2": [100.0] * n,
        "roof_surface_area_m2": [105.0] * n,
        "roof_material": ["metal"] * n,
        "annual_ghi_kwh_m2": [1850.0] * n,
        "absorptance_before": [0.70] * n,
        "energy_saved_kwh_yr": [200.0] * n,
        "co2_saved_kg_yr": [158.0] * n,
        "electricity_saved_kwh_yr": [30.3] * n,
        "co2_electricity_saved_kg_yr": [23.9] * n,
        "heat_to_interior_kwh_yr": [130.0] * n,
        "cooling_load_reduction_kwh_yr": [91.0] * n,
    })


@pytest.fixture
def fake_output_dir(tmp_path, monkeypatch):
    """Patch OUTPUT_DIR to a temp directory and plant a Stage 3 parquet."""
    monkeypatch.setattr(cs, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("config.settings.OUTPUT_DIR", tmp_path)

    df = _make_stage3_df()
    df.to_parquet(tmp_path / "stage3_carlton.parquet", index=False)
    df.to_parquet(tmp_path / "stage2_richmond.parquet", index=False)
    return tmp_path


class TestLoadSuburb:
    def test_prefers_stage3_over_stage2(self, fake_output_dir):
        df, stage = cs._load_suburb("carlton")
        assert stage == 3
        assert len(df) == 100

    def test_falls_back_to_stage2(self, fake_output_dir):
        df, stage = cs._load_suburb("richmond")
        assert stage == 2

    def test_missing_suburb_returns_none(self, fake_output_dir):
        df, stage = cs._load_suburb("no_such_suburb")
        assert df is None
        assert stage == 0

    def test_force_stage2_skips_stage3(self, fake_output_dir):
        # Plant a stage2 parquet too for carlton
        _make_stage3_df().to_parquet(fake_output_dir / "stage2_carlton.parquet", index=False)
        df, stage = cs._load_suburb("carlton", force_stage=2)
        assert stage == 2


class TestBuildComparisonTable:
    def test_returns_dataframe_with_expected_columns(self, fake_output_dir):
        df = cs.build_comparison_table()
        assert not df.empty
        for col in ["suburb", "n_buildings", "total_electricity_saved_kwh_yr", "equiv_households"]:
            assert col in df.columns

    def test_sorted_by_electricity_descending(self, fake_output_dir):
        df = cs.build_comparison_table()
        elec = df["total_electricity_saved_kwh_yr"].tolist()
        assert elec == sorted(elec, reverse=True)

    def test_elec_per_m2_positive(self, fake_output_dir):
        df = cs.build_comparison_table()
        assert (df["elec_per_m2_kwh_yr"] > 0).all()

    def test_equiv_households_consistent(self, fake_output_dir):
        df = cs.build_comparison_table()
        for _, row in df.iterrows():
            expected = row["total_electricity_saved_kwh_yr"] / cs._HOUSEHOLD_KWH_YR
            # Allow rounding error of up to 0.1 household (table rounds to 1 dp)
            assert math.isclose(row["equiv_households"], expected, abs_tol=0.1)

    def test_empty_when_no_outputs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cs, "OUTPUT_DIR", tmp_path)
        df = cs.build_comparison_table()
        assert df.empty


class TestBuildComparisonCharts:
    def test_chart_png_created(self, fake_output_dir):
        df = cs.build_comparison_table()
        path = cs.build_comparison_charts(df)
        assert path.exists()
        assert path.suffix == ".png"


class TestBuildComparisonHtml:
    def test_html_created(self, fake_output_dir):
        df = cs.build_comparison_table()
        chart = fake_output_dir / "suburb_comparison.png"
        chart.write_bytes(b"")  # dummy file
        path = cs.build_comparison_html(df, chart)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Raising Rooves" in content
        assert "Carlton" in content
