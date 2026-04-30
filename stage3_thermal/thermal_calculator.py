"""
Stage 3 thermal calculator for the Raising Rooves pipeline.

Converts absorbed solar reduction (from Stage 2) into cooling electricity
savings using a three-step building thermal physics chain:

    energy_saved_absorbed (kWh/yr, from Stage 2)
        → heat_to_interior_kwh_yr     (roof thermal resistance / conductance)
        → cooling_load_reduction_kwh_yr  (fraction reaching the cooling system)
        → electricity_saved_kwh_yr    (HVAC coefficient of performance)

All parameter defaults and their sources are documented below.
"""

from config.settings import GRID_EMISSIONS_FACTOR_KG_KWH

# ── Default thermal parameters ────────────────────────────────────────────────
# These are building-class defaults; adjusted per building type below.

# Fraction of absorbed roof heat that conducts to the building interior.
# The rest is re-radiated or convected away at the roof surface before reaching
# the ceiling/attic. Value derived from AS 4859.1 roof-assembly modelling and
# CSIRO "Cool Roofs for Australian Homes" (2012, p. 14).
HEAT_TRANSFER_FRACTION = 0.65

# Fraction of interior heat gain from the roof that directly drives cooling
# demand. The remainder is offset by ventilation, thermal mass buffering, or
# night purging. Based on NatHERS 6-star house modelling for Melbourne climate.
COOLING_FRACTION = 0.70

# Coefficient of performance for a typical split-system air conditioner.
# Australian residential units: 2.5–3.5 COP at Melbourne summer conditions.
# GEMS Determination 2019 minimum is 3.0 for a 3.5 kW unit; used as default.
HVAC_COP_RESIDENTIAL = 3.0

# Higher COP for commercial/office buildings fitted with VRF or central chiller
# plant. AIRAH DA19 commercial baseline for Melbourne office stock.
HVAC_COP_COMMERCIAL = 4.0

# Reduced transfer fraction for buildings with 4+ storeys. Additional concrete
# floors and structural thermal mass attenuate roof heat before it reaches
# occupied spaces. Conservative estimate; literature range 0.30–0.50.
HEAT_TRANSFER_FRACTION_MULTISTOREY = 0.40

# Melbourne grid emissions intensity used for CO2 accounting (kg CO2-e / kWh).
# Re-used from Stage 2 — AEMO Victorian grid average 2023.
_CO2_FACTOR = GRID_EMISSIONS_FACTOR_KG_KWH

# Building types treated as commercial for COP adjustment.
_COMMERCIAL_TYPES = frozenset(
    {"commercial", "office", "retail", "industrial", "warehouse"}
)


def calculate_thermal_benefit(
    energy_saved_kwh_yr: float,
    roof_material: str | None = None,
    building_type: str | None = None,
    levels: int | None = None,
) -> dict:
    """
    Convert absorbed solar reduction into cooling electricity savings.

    Takes the Stage 2 ``energy_saved_kwh_yr`` (absorbed solar delta due to cool
    roof treatment) and propagates it through:
      1. Roof-to-interior heat transfer (``HEAT_TRANSFER_FRACTION``)
      2. Fraction driving active cooling demand (``COOLING_FRACTION``)
      3. HVAC efficiency (``HVAC_COP``)

    Building-type adjustments applied:
    - Commercial/office buildings: higher HVAC COP (4.0 vs 3.0).
    - 4+ storey buildings: lower heat transfer fraction (0.40 vs 0.65).

    Args:
        energy_saved_kwh_yr: Absorbed solar reduction from Stage 2 (kWh/yr).
            Zero or negative → all output columns are zero (no benefit).
        roof_material: Roof material tag from Stage 1 (not currently used in
            calculation but reserved for future material-specific U-values).
        building_type: Building type string from Stage 1 (e.g. "residential",
            "commercial", "office"). Used to select HVAC COP.
        levels: Number of building storeys from Stage 1. Used to select heat
            transfer fraction for tall buildings.

    Returns:
        Dict with keys:
            heat_to_interior_kwh_yr       (float, rounded to 1 dp)
            cooling_load_reduction_kwh_yr (float, rounded to 1 dp)
            electricity_saved_kwh_yr      (float, rounded to 1 dp)
            co2_electricity_saved_kg_yr   (float, rounded to 1 dp)
    """
    # Clamp: no negative savings (already a cool roof produces zero in Stage 2,
    # but guard here in case of floating-point residuals)
    energy_saved = max(0.0, energy_saved_kwh_yr)

    # ── Parameter selection ───────────────────────────────────────────────────
    btype = (building_type or "").lower().strip()
    is_commercial = btype in _COMMERCIAL_TYPES

    hvac_cop = HVAC_COP_COMMERCIAL if is_commercial else HVAC_COP_RESIDENTIAL

    # Tall buildings have more thermal mass; less roof heat reaches occupants
    try:
        n_levels = int(levels) if levels is not None else 1
    except (ValueError, TypeError):
        n_levels = 1

    heat_fraction = (
        HEAT_TRANSFER_FRACTION_MULTISTOREY if n_levels >= 4 else HEAT_TRANSFER_FRACTION
    )

    # ── Physics chain ─────────────────────────────────────────────────────────
    heat_to_interior = energy_saved * heat_fraction
    cooling_load_reduction = heat_to_interior * COOLING_FRACTION
    electricity_saved = cooling_load_reduction / hvac_cop
    co2_electricity_saved = electricity_saved * _CO2_FACTOR

    return {
        "heat_to_interior_kwh_yr": round(heat_to_interior, 1),
        "cooling_load_reduction_kwh_yr": round(cooling_load_reduction, 1),
        "electricity_saved_kwh_yr": round(electricity_saved, 1),
        "co2_electricity_saved_kg_yr": round(co2_electricity_saved, 1),
    }
