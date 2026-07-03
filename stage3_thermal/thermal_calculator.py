"""
Stage 3 thermal calculator for the Raising Rooves pipeline.

Converts absorbed solar reduction (from Stage 2) into cooling electricity
savings using a three-step building thermal physics chain:

    energy_saved_absorbed (kWh/yr, from Stage 2)
        → heat_to_interior_kwh_yr     (roof thermal resistance / conductance)
        → cooling_load_reduction_kwh_yr  (fraction reaching the cooling system)
        → electricity_saved_kwh_yr    (HVAC coefficient of performance)

The roof-to-interior step derives its conductance PER BUILDING from an inferred
roof thermal resistance R_roof (m²·K/W), following the roof-only heat-ingress
framing in Maggie's model:

    U_roof   = 1 / R_roof
    fraction = U_roof / (U_roof + H_OUTSIDE_W_M2K)

Stage 1 provides no construction-age field, so R_roof is inferred from the
attributes we have (building_type, levels, roof_material). All parameter
defaults and their sources are documented in config/settings.py.
"""

from config.settings import (
    COOLING_FRACTION,
    GRID_EMISSIONS_FACTOR_KG_KWH,
    H_OUTSIDE_W_M2K,
    HVAC_COP_COMMERCIAL,
    HVAC_COP_RESIDENTIAL,
    MULTISTOREY_ATTENUATION,
    R_ROOF_BY_CATEGORY,
    R_ROOF_DEFAULT,
    R_ROOF_METAL_RESIDENTIAL,
)

_CO2_FACTOR = GRID_EMISSIONS_FACTOR_KG_KWH

# Building types treated as commercial for COP adjustment.
_COMMERCIAL_TYPES = frozenset(
    {"commercial", "office", "retail", "industrial", "warehouse"}
)

# OSM building tags that denote non-residential stock for R_roof selection.
_NON_RESIDENTIAL_TYPES = frozenset(
    {"commercial", "office", "retail", "industrial", "warehouse"}
)

# roof_material labels (from Stage 1) that denote a metal roof.
_METAL_MATERIALS = frozenset({"metal", "metal_light", "metal_dark", "corrugated_iron"})


def _r_roof_for_building(
    building_type: str | None,
    levels: int | None,
    roof_material: str | None,
) -> float:
    """
    Infer a roof thermal resistance R_roof (m²·K/W) from Stage 1 attributes.

    Priority: non-residential category → residential (with a metal-roof nudge for
    likely older/less-insulated stock) → default. Stage 1 has no construction-age
    field, so this is a documented proxy, not a measured value.

    Args:
        building_type: OSM building tag from Stage 1 (may be None).
        levels: Number of storeys from Stage 1 (unused for R_roof selection today;
            multistorey attenuation is applied separately). Accepted for a stable
            signature and future refinement.
        roof_material: Roof material label from Stage 1 (may be None).

    Returns:
        R_roof in m²·K/W.
    """
    btype = (
        "" if not building_type or isinstance(building_type, float)
        else str(building_type)
    ).lower().strip()
    material = (
        "" if not roof_material or isinstance(roof_material, float)
        else str(roof_material)
    ).lower().strip()

    if btype in _NON_RESIDENTIAL_TYPES:
        return R_ROOF_BY_CATEGORY["commercial"]

    # Everything else is treated as residential stock.
    if material in _METAL_MATERIALS:
        return R_ROOF_METAL_RESIDENTIAL

    if btype in {"residential", "house", "detached", "apartments", "yes"}:
        return R_ROOF_BY_CATEGORY["residential"]

    return R_ROOF_DEFAULT


def _heat_fraction_from_r_roof(r_roof: float) -> float:
    """
    Fraction of the absorbed-solar delta that conducts to the interior.

    fraction = U_roof / (U_roof + H_OUTSIDE_W_M2K),  U_roof = 1 / R_roof.

    Args:
        r_roof: Roof thermal resistance in m²·K/W (must be > 0).

    Returns:
        Unitless heat-transfer fraction in (0, 1).
    """
    r = r_roof if r_roof and r_roof > 0 else R_ROOF_DEFAULT
    u_roof = 1.0 / r
    return u_roof / (u_roof + H_OUTSIDE_W_M2K)


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
      1. Roof-to-interior heat transfer (fraction derived from R_roof)
      2. Fraction driving active cooling demand (``COOLING_FRACTION``)
      3. HVAC efficiency (``HVAC_COP``)

    Building-type adjustments applied:
    - Commercial/office buildings: higher HVAC COP (4.0 vs 3.0).
    - R_roof inferred per building drives the heat-transfer fraction
      (lower R_roof → more heat reaches interior → more benefit).
    - 4+ storey buildings: extra attenuation multiplier on the heat path.

    Args:
        energy_saved_kwh_yr: Absorbed solar reduction from Stage 2 (kWh/yr).
            Zero or negative → all output columns are zero (no benefit).
        roof_material: Roof material tag from Stage 1. Used (with building_type)
            to infer R_roof — metal residential roofs are treated as less insulated.
        building_type: Building type string from Stage 1 (e.g. "residential",
            "commercial", "office"). Used to select HVAC COP and R_roof.
        levels: Number of building storeys from Stage 1. Used to apply the
            multistorey heat-path attenuation for tall buildings.

    Returns:
        Dict with keys:
            roof_r_value_m2k              (float, inferred R_roof)
            heat_transfer_fraction        (float, effective fraction incl. multistorey)
            heat_to_interior_kwh_yr       (float, rounded to 1 dp)
            cooling_load_reduction_kwh_yr (float, rounded to 1 dp)
            electricity_saved_kwh_yr      (float, rounded to 1 dp)
            co2_electricity_saved_kg_yr   (float, rounded to 1 dp)
    """
    # Clamp: no negative savings (already a cool roof produces zero in Stage 2,
    # but guard here in case of floating-point residuals)
    energy_saved = max(0.0, energy_saved_kwh_yr)

    # ── Parameter selection ───────────────────────────────────────────────────
    btype = ("" if not building_type or (isinstance(building_type, float)) else str(building_type)).lower().strip()
    is_commercial = btype in _COMMERCIAL_TYPES

    hvac_cop = HVAC_COP_COMMERCIAL if is_commercial else HVAC_COP_RESIDENTIAL

    try:
        n_levels = int(levels) if levels is not None else 1
    except (ValueError, TypeError):
        n_levels = 1

    # Per-building roof insulation drives the base conductance fraction.
    r_roof = _r_roof_for_building(building_type, levels, roof_material)
    base_fraction = _heat_fraction_from_r_roof(r_roof)

    # Tall buildings have more thermal mass; less roof heat reaches occupants.
    multistorey = MULTISTOREY_ATTENUATION if n_levels >= 4 else 1.0
    heat_fraction = base_fraction * multistorey

    # ── Physics chain ─────────────────────────────────────────────────────────
    heat_to_interior = energy_saved * heat_fraction
    cooling_load_reduction = heat_to_interior * COOLING_FRACTION
    electricity_saved = cooling_load_reduction / hvac_cop
    co2_electricity_saved = electricity_saved * _CO2_FACTOR

    return {
        "roof_r_value_m2k": round(r_roof, 2),
        "heat_transfer_fraction": round(heat_fraction, 4),
        "heat_to_interior_kwh_yr": round(heat_to_interior, 1),
        "cooling_load_reduction_kwh_yr": round(cooling_load_reduction, 1),
        "electricity_saved_kwh_yr": round(electricity_saved, 1),
        "co2_electricity_saved_kg_yr": round(co2_electricity_saved, 1),
    }
