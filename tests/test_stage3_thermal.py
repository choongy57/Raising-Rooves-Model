"""
Tests for stage3_thermal/thermal_calculator.py.

Covers:
- Zero saving when absorbed energy is zero (already cool roof in Stage 2)
- Correct COP adjustment for commercial buildings
- Correct heat transfer fraction adjustment for tall (4+ storey) buildings
- Output columns all present
- Physics chain arithmetic is self-consistent
- Negative energy_saved_kwh_yr is clamped to zero
"""

import pytest

from stage3_thermal.thermal_calculator import (
    COOLING_FRACTION,
    HEAT_TRANSFER_FRACTION,
    HEAT_TRANSFER_FRACTION_MULTISTOREY,
    HVAC_COP_COMMERCIAL,
    HVAC_COP_RESIDENTIAL,
    calculate_thermal_benefit,
)


EXPECTED_KEYS = {
    "heat_to_interior_kwh_yr",
    "cooling_load_reduction_kwh_yr",
    "electricity_saved_kwh_yr",
    "co2_electricity_saved_kg_yr",
}


class TestOutputKeys:
    def test_all_keys_present_default(self):
        result = calculate_thermal_benefit(energy_saved_kwh_yr=1000.0)
        assert EXPECTED_KEYS == set(result.keys())

    def test_all_keys_present_commercial(self):
        result = calculate_thermal_benefit(
            energy_saved_kwh_yr=500.0, building_type="commercial"
        )
        assert EXPECTED_KEYS == set(result.keys())


class TestZeroSaving:
    def test_zero_input_gives_zero_outputs(self):
        """A building that already meets cool roof spec has zero Stage 2 saving → zero Stage 3."""
        result = calculate_thermal_benefit(energy_saved_kwh_yr=0.0)
        assert result["heat_to_interior_kwh_yr"] == 0.0
        assert result["cooling_load_reduction_kwh_yr"] == 0.0
        assert result["electricity_saved_kwh_yr"] == 0.0
        assert result["co2_electricity_saved_kg_yr"] == 0.0

    def test_negative_input_clamped_to_zero(self):
        """Negative absorbed saving (shouldn't happen, but must not produce negative electricity)."""
        result = calculate_thermal_benefit(energy_saved_kwh_yr=-500.0)
        assert result["electricity_saved_kwh_yr"] == 0.0


class TestResidentialPhysics:
    def test_physics_chain_arithmetic(self):
        """
        Manual walk-through with round numbers.
        energy_saved = 1000 kWh/yr, default residential parameters:
          heat_to_interior   = 1000 * 0.65 = 650
          cooling_load       = 650  * 0.70 = 455
          electricity_saved  = 455  / 3.0  ≈ 151.7
        """
        result = calculate_thermal_benefit(energy_saved_kwh_yr=1000.0)
        assert result["heat_to_interior_kwh_yr"] == pytest.approx(
            1000.0 * HEAT_TRANSFER_FRACTION, abs=0.2
        )
        assert result["cooling_load_reduction_kwh_yr"] == pytest.approx(
            1000.0 * HEAT_TRANSFER_FRACTION * COOLING_FRACTION, abs=0.2
        )
        assert result["electricity_saved_kwh_yr"] == pytest.approx(
            1000.0 * HEAT_TRANSFER_FRACTION * COOLING_FRACTION / HVAC_COP_RESIDENTIAL,
            abs=0.2,
        )

    def test_electricity_less_than_absorbed_solar(self):
        """Electricity saving must always be less than absorbed solar saving."""
        result = calculate_thermal_benefit(energy_saved_kwh_yr=2000.0)
        assert result["electricity_saved_kwh_yr"] < 2000.0


class TestCommercialCopAdjustment:
    @pytest.mark.parametrize("btype", ["commercial", "office", "retail"])
    def test_commercial_higher_cop_means_lower_electricity_saving(self, btype):
        """
        Commercial COP is higher than residential COP.
        Higher COP → same cooling load, but less electricity consumed.
        electricity_saved = cooling_load / COP, so larger COP → smaller electricity_saved.
        """
        res_result = calculate_thermal_benefit(
            energy_saved_kwh_yr=1000.0, building_type="residential"
        )
        com_result = calculate_thermal_benefit(
            energy_saved_kwh_yr=1000.0, building_type=btype
        )
        # Commercial has higher COP → less electricity consumed per unit cooling
        assert com_result["electricity_saved_kwh_yr"] < res_result["electricity_saved_kwh_yr"]

    def test_commercial_cop_arithmetic(self):
        """Verify exact COP used for commercial buildings."""
        result = calculate_thermal_benefit(
            energy_saved_kwh_yr=1000.0, building_type="commercial"
        )
        expected_elec = (
            1000.0 * HEAT_TRANSFER_FRACTION * COOLING_FRACTION / HVAC_COP_COMMERCIAL
        )
        assert result["electricity_saved_kwh_yr"] == pytest.approx(expected_elec, abs=0.2)

    def test_case_insensitive_building_type(self):
        """Building type lookup must be case-insensitive."""
        lower = calculate_thermal_benefit(1000.0, building_type="commercial")
        upper = calculate_thermal_benefit(1000.0, building_type="COMMERCIAL")
        assert lower["electricity_saved_kwh_yr"] == upper["electricity_saved_kwh_yr"]


class TestMultistoreyHeatFraction:
    def test_4_storey_uses_reduced_fraction(self):
        """Buildings with 4+ levels get lower heat transfer fraction."""
        low_rise = calculate_thermal_benefit(energy_saved_kwh_yr=1000.0, levels=2)
        high_rise = calculate_thermal_benefit(energy_saved_kwh_yr=1000.0, levels=4)
        # High-rise should have less heat reaching interior
        assert high_rise["heat_to_interior_kwh_yr"] < low_rise["heat_to_interior_kwh_yr"]

    def test_4_storey_fraction_arithmetic(self):
        result = calculate_thermal_benefit(energy_saved_kwh_yr=1000.0, levels=5)
        assert result["heat_to_interior_kwh_yr"] == pytest.approx(
            1000.0 * HEAT_TRANSFER_FRACTION_MULTISTOREY, abs=0.2
        )

    def test_3_storey_uses_standard_fraction(self):
        result = calculate_thermal_benefit(energy_saved_kwh_yr=1000.0, levels=3)
        assert result["heat_to_interior_kwh_yr"] == pytest.approx(
            1000.0 * HEAT_TRANSFER_FRACTION, abs=0.2
        )


class TestCo2Chain:
    def test_co2_positive_when_electricity_positive(self):
        result = calculate_thermal_benefit(energy_saved_kwh_yr=500.0)
        assert result["co2_electricity_saved_kg_yr"] > 0.0

    def test_co2_zero_when_electricity_zero(self):
        result = calculate_thermal_benefit(energy_saved_kwh_yr=0.0)
        assert result["co2_electricity_saved_kg_yr"] == 0.0


class TestNoneInputs:
    def test_none_building_type_uses_residential_defaults(self):
        explicit = calculate_thermal_benefit(1000.0, building_type="residential")
        none_type = calculate_thermal_benefit(1000.0, building_type=None)
        assert explicit["electricity_saved_kwh_yr"] == none_type["electricity_saved_kwh_yr"]

    def test_none_levels_treated_as_low_rise(self):
        explicit_1 = calculate_thermal_benefit(1000.0, levels=1)
        none_levels = calculate_thermal_benefit(1000.0, levels=None)
        assert explicit_1["electricity_saved_kwh_yr"] == none_levels["electricity_saved_kwh_yr"]
