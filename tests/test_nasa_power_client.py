"""
Unit tests for stage2_irradiance.nasa_power_client and
stage2_irradiance.irradiance_loader.load_nasa_power_irradiance.

All tests use unittest.mock to avoid real network calls.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from stage2_irradiance.nasa_power_client import (
    fetch_annual_ghi,
    fetch_suburb_ghi_grid,
)
from stage2_irradiance.irradiance_loader import load_nasa_power_irradiance


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_power_response(ann_daily: float) -> dict:
    """Build a minimal NASA POWER API JSON response."""
    return {
        "properties": {
            "parameter": {
                "ALLSKY_SFC_SW_DWN": {
                    "JAN": ann_daily * 0.9,
                    "FEB": ann_daily * 0.95,
                    "MAR": ann_daily,
                    "APR": ann_daily * 1.05,
                    "MAY": ann_daily * 1.1,
                    "JUN": ann_daily * 1.2,
                    "JUL": ann_daily * 1.15,
                    "AUG": ann_daily * 1.05,
                    "SEP": ann_daily * 1.0,
                    "OCT": ann_daily * 0.95,
                    "NOV": ann_daily * 0.9,
                    "DEC": ann_daily * 0.85,
                    "ANN": ann_daily,
                }
            }
        }
    }


# ── fetch_annual_ghi ─────────────────────────────────────────────────────────

class TestFetchAnnualGhi:

    def _mock_response(self, ann_daily: float) -> MagicMock:
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = _make_power_response(ann_daily)
        return mock

    def test_converts_daily_to_annual(self):
        """fetch_annual_ghi should multiply kWh/m²/day by 365."""
        with patch("requests.get", return_value=self._mock_response(4.932)) as mock_get:
            result = fetch_annual_ghi(-37.7998, 144.9667)

        assert result == round(4.932 * 365, 1)
        mock_get.assert_called_once()

    def test_returns_reasonable_melbourne_value(self):
        """Sanity check: Melbourne GHI should be in 1700–2000 kWh/m²/yr range."""
        # 4.794 kWh/m²/day → ~1750 kWh/m²/yr
        with patch("requests.get", return_value=self._mock_response(4.794)):
            result = fetch_annual_ghi(-37.7998, 144.9667)

        assert result is not None
        assert 1700.0 <= result <= 2000.0

    def test_returns_none_on_missing_ann_key(self):
        """If the ANN key is absent, fetch_annual_ghi should return None."""
        mock = MagicMock()
        mock.raise_for_status.return_value = None
        mock.json.return_value = {"properties": {"parameter": {"ALLSKY_SFC_SW_DWN": {}}}}

        with patch("requests.get", return_value=mock):
            result = fetch_annual_ghi(-37.7998, 144.9667)

        assert result is None

    def test_returns_none_on_negative_fill_value(self):
        """NASA POWER uses -999 as a fill value — should return None."""
        with patch("requests.get", return_value=self._mock_response(-999.0)):
            result = fetch_annual_ghi(-37.7998, 144.9667)

        assert result is None

    def test_returns_none_on_network_error(self):
        """Network errors should be caught and None returned (no exception raised)."""
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.exceptions.ConnectionError("down")):
            result = fetch_annual_ghi(-37.7998, 144.9667)

        assert result is None

    def test_returns_none_on_timeout(self):
        """Timeout errors should be caught and None returned."""
        import requests as req_lib
        with patch("requests.get", side_effect=req_lib.exceptions.Timeout("timeout")):
            result = fetch_annual_ghi(-37.7998, 144.9667)

        assert result is None

    def test_request_includes_correct_params(self):
        """The correct lat/lon and community should be passed to the API."""
        with patch("requests.get", return_value=self._mock_response(5.0)) as mock_get:
            fetch_annual_ghi(-37.8, 144.97)

        call_kwargs = mock_get.call_args
        params = call_kwargs[1]["params"] if "params" in call_kwargs[1] else call_kwargs[0][1]
        assert params["latitude"] == -37.8
        assert params["longitude"] == 144.97
        assert params["community"] == "RE"
        assert "ALLSKY_SFC_SW_DWN" in params["parameters"]


# ── fetch_suburb_ghi_grid ────────────────────────────────────────────────────

class TestFetchSuburbGhiGrid:

    def _mock_fetch(self, value: float = 1800.0):
        """Patch fetch_annual_ghi to always return a fixed value."""
        return patch(
            "stage2_irradiance.nasa_power_client.fetch_annual_ghi",
            return_value=value,
        )

    def test_returns_dataframe_with_correct_columns(self):
        with self._mock_fetch():
            df = fetch_suburb_ghi_grid(-37.81, 144.955, -37.79, 144.978)

        assert set(df.columns) >= {"lat", "lon", "annual_ghi_kwh_m2"}

    def test_always_includes_centroid(self):
        """The centroid of the bbox should always appear in the output."""
        with self._mock_fetch():
            df = fetch_suburb_ghi_grid(-37.81, 144.955, -37.79, 144.978)

        centre_lat = round((-37.81 + -37.79) / 2.0, 6)
        centre_lon = round((144.955 + 144.978) / 2.0, 6)
        assert any(
            abs(row["lat"] - centre_lat) < 1e-4 and abs(row["lon"] - centre_lon) < 1e-4
            for _, row in df.iterrows()
        ), "Centroid not found in grid output"

    def test_returns_empty_df_when_all_fetches_fail(self):
        with patch(
            "stage2_irradiance.nasa_power_client.fetch_annual_ghi",
            return_value=None,
        ):
            df = fetch_suburb_ghi_grid(-37.81, 144.955, -37.79, 144.978)

        assert df.empty

    def test_ghi_values_match_mock(self):
        """All returned GHI values should equal the mocked value."""
        with self._mock_fetch(value=1823.5):
            df = fetch_suburb_ghi_grid(-37.81, 144.955, -37.79, 144.978)

        assert (df["annual_ghi_kwh_m2"] == 1823.5).all()


# ── load_nasa_power_irradiance ───────────────────────────────────────────────

class TestLoadNasaPowerIrradiance:

    def test_writes_cache_on_first_call(self, tmp_path: Path):
        """First call with no cache should fetch and write a CSV."""
        mock_df = pd.DataFrame([
            {"lat": -37.8, "lon": 144.96, "annual_ghi_kwh_m2": 1800.0},
        ])
        with patch(
            "stage2_irradiance.nasa_power_client.fetch_suburb_ghi_grid",
            return_value=mock_df,
        ):
            result = load_nasa_power_irradiance(
                south=-37.81, west=144.955, north=-37.79, east=144.978,
                suburb_key="test_suburb",
                cache_dir=tmp_path,
            )

        cache_file = tmp_path / "test_suburb_ghi.csv"
        assert cache_file.exists(), "Cache file should have been created"
        assert len(result) == 1
        assert result.iloc[0]["annual_ghi_kwh_m2"] == 1800.0

    def test_reads_cache_on_second_call(self, tmp_path: Path):
        """Second call should read the cache without fetching from API."""
        # Pre-populate cache
        cache_df = pd.DataFrame([
            {"lat": -37.80, "lon": 144.96, "annual_ghi_kwh_m2": 1755.0},
        ])
        cache_file = tmp_path / "cached_suburb_ghi.csv"
        cache_df.to_csv(cache_file, index=False)

        with patch(
            "stage2_irradiance.nasa_power_client.fetch_suburb_ghi_grid",
        ) as mock_fetch:
            result = load_nasa_power_irradiance(
                south=-37.81, west=144.955, north=-37.79, east=144.978,
                suburb_key="cached_suburb",
                cache_dir=tmp_path,
            )
            mock_fetch.assert_not_called()

        assert result.iloc[0]["annual_ghi_kwh_m2"] == 1755.0

    def test_returns_empty_df_when_api_fails(self, tmp_path: Path):
        """If NASA POWER returns empty and no cache exists, return empty DataFrame."""
        with patch(
            "stage2_irradiance.nasa_power_client.fetch_suburb_ghi_grid",
            return_value=pd.DataFrame(columns=["lat", "lon", "annual_ghi_kwh_m2"]),
        ):
            result = load_nasa_power_irradiance(
                south=-37.81, west=144.955, north=-37.79, east=144.978,
                suburb_key="fail_suburb",
                cache_dir=tmp_path,
            )

        assert result.empty
        # Cache file should NOT be written when data is empty
        assert not (tmp_path / "fail_suburb_ghi.csv").exists()

    def test_output_columns_correct(self, tmp_path: Path):
        """Returned DataFrame should have exactly lat, lon, annual_ghi_kwh_m2."""
        mock_df = pd.DataFrame([
            {"lat": -37.80, "lon": 144.96, "annual_ghi_kwh_m2": 1810.0, "extra_col": "x"},
        ])
        with patch(
            "stage2_irradiance.nasa_power_client.fetch_suburb_ghi_grid",
            return_value=mock_df,
        ):
            result = load_nasa_power_irradiance(
                south=-37.81, west=144.955, north=-37.79, east=144.978,
                suburb_key="col_test",
                cache_dir=tmp_path,
            )

        assert list(result.columns) == ["lat", "lon", "annual_ghi_kwh_m2"]

    def test_creates_cache_dir_if_missing(self, tmp_path: Path):
        """load_nasa_power_irradiance should create the cache directory if it doesn't exist."""
        new_dir = tmp_path / "deep" / "nested" / "cache"
        assert not new_dir.exists()

        mock_df = pd.DataFrame([
            {"lat": -37.80, "lon": 144.96, "annual_ghi_kwh_m2": 1800.0},
        ])
        with patch(
            "stage2_irradiance.nasa_power_client.fetch_suburb_ghi_grid",
            return_value=mock_df,
        ):
            load_nasa_power_irradiance(
                south=-37.81, west=144.955, north=-37.79, east=144.978,
                suburb_key="dir_test",
                cache_dir=new_dir,
            )

        assert new_dir.exists()
