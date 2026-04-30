"""
Unit tests for BARRA2 client utilities:
  - stage2_irradiance.barra_client  (URL builder, connection test)
  - stage2_irradiance.irradiance_processor.compute_annual_ghi_from_hourly
  - stage2_irradiance.temperature_processor.compute_cooling_degree_hours

All tests mock the network and xarray so that no real NCI access is required.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from stage2_irradiance.barra_client import (
    _build_barra2_catalog_url,
    _build_barra2_url,
    test_barra2_connection as check_barra2_connection,
)
from stage2_irradiance.irradiance_processor import compute_annual_ghi_from_hourly
from stage2_irradiance.temperature_processor import compute_cooling_degree_hours

KELVIN_OFFSET = 273.15


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_irradiance_ds(mean_w_m2: float, n_hours: int = 8760, variable: str = "rsds") -> xr.Dataset:
    """Build a synthetic hourly irradiance Dataset with a constant flux value."""
    times = pd.date_range("2015-01-01", periods=n_hours, freq="1h")
    values = np.full(n_hours, mean_w_m2)
    da = xr.DataArray(values, coords={"time": times}, dims=["time"])
    return xr.Dataset({variable: da})


def _make_temperature_ds(temp_k_values: list[float], variable: str = "tas") -> xr.Dataset:
    """Build a synthetic hourly temperature Dataset from a list of Kelvin values."""
    n = len(temp_k_values)
    times = pd.date_range("2015-01-01", periods=n, freq="1h")
    da = xr.DataArray(np.array(temp_k_values, dtype=float), coords={"time": times}, dims=["time"])
    return xr.Dataset({variable: da})


def _make_multi_year_ds(variable: str, year_values: dict[int, float], n_hours: int = 8760) -> xr.Dataset:
    """
    Build a multi-year Dataset where each year has a constant value.

    Args:
        variable: Variable name in the output Dataset.
        year_values: Dict mapping year → constant value for all hours in that year.
        n_hours: Number of hourly timesteps per year (default 8760).
    """
    all_times = pd.DatetimeIndex([])
    all_values: list[float] = []
    for yr in sorted(year_values):
        times = pd.date_range(f"{yr}-01-01", periods=n_hours, freq="1h")
        all_times = all_times.append(times)
        all_values.extend([year_values[yr]] * n_hours)
    da = xr.DataArray(np.array(all_values), coords={"time": all_times}, dims=["time"])
    return xr.Dataset({variable: da})


# ── URL builder tests ────────────────────────────────────────────────────────

class TestBuildBarra2Url:

    def test_url_contains_correct_domain(self):
        """URL must use AUS-11 domain, not AUS-04."""
        url = _build_barra2_url("solar_irradiance", 2015, 6)
        assert "AUS-11" in url
        assert "AUS-04" not in url

    def test_url_contains_correct_variable(self):
        """URL for solar_irradiance must use rsds, not av_swsfcdown."""
        url = _build_barra2_url("solar_irradiance", 2015, 6)
        assert "rsds" in url
        assert "av_swsfcdown" not in url

    def test_url_contains_correct_temperature_variable(self):
        """URL for temperature_2m must use tas, not temp_scrn."""
        url = _build_barra2_url("temperature_2m", 2020, 1)
        assert "tas" in url
        assert "temp_scrn" not in url

    def test_url_year_month_formatting(self):
        """Year and zero-padded month must appear in the filename portion."""
        url = _build_barra2_url("solar_irradiance", 2018, 3)
        assert "201803-201803" in url

    def test_url_uses_latest_version(self):
        """URL must use 'latest' as the version token, not a hardcoded date tag."""
        url = _build_barra2_url("solar_irradiance", 2015, 1)
        assert "/latest/" in url

    def test_url_uses_opendap_base(self):
        """URL must point to the OPeNDAP (dodsC) service, not the catalog."""
        url = _build_barra2_url("solar_irradiance", 2015, 1)
        assert "dodsC" in url
        assert "catalog" not in url

    def test_catalog_url_uses_catalog_base(self):
        """Catalog URL must point to the THREDDS catalog endpoint."""
        url = _build_barra2_catalog_url("solar_irradiance")
        assert "thredds/catalog" in url
        assert "rsds" in url
        assert "dodsC" not in url


# ── compute_annual_ghi_from_hourly ───────────────────────────────────────────

class TestComputeAnnualGhiFromHourly:

    def test_known_value_single_year(self):
        """
        200 W/m² constant flux x 8760 h / 1000 = 1752.0 kWh/m2/yr.
        """
        ds = _make_irradiance_ds(mean_w_m2=200.0, n_hours=8760)
        result = compute_annual_ghi_from_hourly(ds, variable="rsds")
        assert result == pytest.approx(1752.0, abs=0.1)

    def test_melbourne_typical_range(self):
        """
        Melbourne mean GHI is roughly 200-230 W/m2 → 1750-2015 kWh/m2/yr.
        Use 211.2 W/m2 → ~1850 kWh/m2/yr (close to the project constant).
        """
        ds = _make_irradiance_ds(mean_w_m2=211.2, n_hours=8760)
        result = compute_annual_ghi_from_hourly(ds, variable="rsds")
        assert 1700.0 <= result <= 2100.0

    def test_multi_year_averages_across_years(self):
        """
        Two years: year 2014 = 100 W/m2, year 2015 = 300 W/m2.
        Expected mean = 200 W/m2 → 200 x 8760 / 1000 = 1752.0 kWh/m2/yr.
        """
        ds = _make_multi_year_ds("rsds", {2014: 100.0, 2015: 300.0})
        result = compute_annual_ghi_from_hourly(ds, variable="rsds")
        assert result == pytest.approx(1752.0, abs=0.2)

    def test_year_filter(self):
        """years= parameter restricts computation to specified calendar years."""
        ds = _make_multi_year_ds("rsds", {2014: 100.0, 2015: 300.0})
        result = compute_annual_ghi_from_hourly(ds, variable="rsds", years=[2015])
        # Only year 2015 (300 W/m2) → 300 x 8760 / 1000 = 2628.0
        assert result == pytest.approx(2628.0, abs=0.2)

    def test_raises_on_missing_variable(self):
        """KeyError raised when the variable is not in the dataset."""
        ds = _make_irradiance_ds(200.0)
        with pytest.raises(KeyError):
            compute_annual_ghi_from_hourly(ds, variable="nonexistent")

    def test_raises_on_empty_after_filter(self):
        """ValueError raised when year filter leaves no data."""
        ds = _make_irradiance_ds(200.0, n_hours=8760)
        with pytest.raises(ValueError):
            compute_annual_ghi_from_hourly(ds, variable="rsds", years=[9999])

    def test_zero_flux_returns_zero(self):
        """A dataset of all-zero flux should return 0.0 kWh/m2/yr."""
        ds = _make_irradiance_ds(0.0, n_hours=8760)
        result = compute_annual_ghi_from_hourly(ds, variable="rsds")
        assert result == 0.0


# ── compute_cooling_degree_hours ─────────────────────────────────────────────

class TestComputeCoolingDegreeHours:

    def test_known_value_constant_temperature(self):
        """
        Constant 25°C (298.15 K) for 8760 hours, base 18.5°C:
        CDH = 8760 x (25 - 18.5) = 8760 x 6.5 = 56940.0.
        """
        temp_k = [25.0 + KELVIN_OFFSET] * 8760
        ds = _make_temperature_ds(temp_k)
        result = compute_cooling_degree_hours(ds, variable="tas", base_temp_c=18.5)
        assert result == pytest.approx(56940.0, abs=1.0)

    def test_temperatures_below_base_contribute_zero(self):
        """
        Constant 10°C is below 18.5°C base — CDH should be 0.
        """
        temp_k = [10.0 + KELVIN_OFFSET] * 8760
        ds = _make_temperature_ds(temp_k)
        result = compute_cooling_degree_hours(ds, variable="tas", base_temp_c=18.5)
        assert result == 0.0

    def test_kelvin_input_converted_correctly(self):
        """
        Values clearly in Kelvin (> 100) should be converted to Celsius.
        290 K = 16.85°C < 18.5°C base → CDH = 0.
        """
        temp_k = [290.0] * 8760  # 16.85 degrees C
        ds = _make_temperature_ds(temp_k)
        result = compute_cooling_degree_hours(ds, variable="tas", base_temp_c=18.5)
        assert result == 0.0

    def test_mixed_temperatures(self):
        """
        Half year at 20°C (above base by 1.5) and half at 10°C (below base):
        CDH = 4380 x 1.5 + 4380 x 0 = 6570.
        """
        above = [20.0 + KELVIN_OFFSET] * 4380
        below = [10.0 + KELVIN_OFFSET] * 4380
        ds = _make_temperature_ds(above + below)
        result = compute_cooling_degree_hours(ds, variable="tas", base_temp_c=18.5)
        assert result == pytest.approx(6570.0, abs=1.0)

    def test_custom_base_temperature(self):
        """
        Constant 22°C, custom base 20°C:
        CDH = 8760 x 2.0 = 17520.
        """
        temp_k = [22.0 + KELVIN_OFFSET] * 8760
        ds = _make_temperature_ds(temp_k)
        result = compute_cooling_degree_hours(ds, variable="tas", base_temp_c=20.0)
        assert result == pytest.approx(17520.0, abs=1.0)

    def test_raises_on_missing_variable(self):
        """KeyError raised when the variable is not in the dataset."""
        ds = _make_temperature_ds([295.0] * 100)
        with pytest.raises(KeyError):
            compute_cooling_degree_hours(ds, variable="nonexistent")

    def test_multi_year_averages_across_years(self):
        """
        Two years: year 2014 all at base (CDH=0), year 2015 all at base+1 (CDH=8760).
        Mean CDH = 4380.
        """
        base_k = 18.5 + KELVIN_OFFSET
        ds = _make_multi_year_ds("tas", {2014: base_k, 2015: base_k + 1.0})
        result = compute_cooling_degree_hours(ds, variable="tas", base_temp_c=18.5)
        assert result == pytest.approx(4380.0, abs=1.0)


# ── test_barra2_connection ────────────────────────────────────────────────────

class TestBarra2Connection:

    def test_returns_false_on_connection_error(self):
        """Network unavailable → returns False without raising."""
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Network unreachable"),
        ):
            result = check_barra2_connection()
        assert result is False

    def test_returns_false_on_http_error(self):
        """HTTP 403 or 404 from the catalog → returns False."""
        import urllib.error
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=403, msg="Forbidden", hdrs=None, fp=None
            ),
        ):
            result = check_barra2_connection()
        assert result is False

    def test_returns_true_on_http_200(self):
        """HTTP 200 response → returns True."""
        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch(
            "urllib.request.urlopen",
            return_value=mock_resp,
        ):
            result = check_barra2_connection()
        assert result is True

    def test_returns_false_on_timeout(self):
        """OSError (timeout) → returns False without raising."""
        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("timed out"),
        ):
            result = check_barra2_connection()
        assert result is False
