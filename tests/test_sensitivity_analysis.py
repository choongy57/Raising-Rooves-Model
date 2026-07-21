"""
Tests for tools/sensitivity_analysis.py.

The critical guarantee is *faithfulness*: the vectorised chain in the tool must
reproduce the real Stage 2 + Stage 3 calc functions. If the model changes and
the tool drifts, these tests fail. The rest assert the qualitative direction of
each parameter (a multiplicative model has an unambiguous sign per knob).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from config.settings import MELBOURNE_DEFAULT_GHI_KWH_M2_YR
from tools.sensitivity_analysis import (
    Params,
    build_baseline,
    crosscheck_against_pipeline,
    monte_carlo,
    oat_tornado,
    suburb_totals,
)


def _synthetic_df() -> pd.DataFrame:
    """A small mixed-stock dataframe covering every R_roof / COP branch."""
    return pd.DataFrame([
        # residential tile — default residential R_roof
        {"area_m2": 120.0, "pitch_deg": 22.5, "building_type": "house",
         "roof_material": "concrete_tile", "roof_colour": "red", "levels": 1,
         "absorptance_estimate": 0.75, "absorptance_uncertainty": 0.12},
        # residential metal — metal-residential R_roof nudge
        {"area_m2": 90.0, "pitch_deg": 15.0, "building_type": "residential",
         "roof_material": "metal_dark", "roof_colour": "dark_grey", "levels": 1,
         "absorptance_estimate": 0.85, "absorptance_uncertainty": 0.08},
        # commercial — commercial R_roof + commercial COP
        {"area_m2": 800.0, "pitch_deg": 0.0, "building_type": "commercial",
         "roof_material": "metal_light", "roof_colour": "light_grey", "levels": 2,
         "absorptance_estimate": 0.45, "absorptance_uncertainty": 0.10},
        # multistorey — attenuation branch, no HSV estimate (labels fallback)
        {"area_m2": 500.0, "pitch_deg": 0.0, "building_type": "apartments",
         "roof_material": "concrete_tile", "roof_colour": None, "levels": 6,
         "absorptance_estimate": None, "absorptance_uncertainty": None},
    ])


def test_params_defaults_mirror_settings():
    from config import settings
    p = Params()
    assert p.ghi == settings.MELBOURNE_DEFAULT_GHI_KWH_M2_YR
    assert p.abs_after == settings.COOL_ROOF_ABSORPTANCE
    assert p.h_outside == settings.H_OUTSIDE_W_M2K
    assert p.cop_res == settings.HVAC_COP_RESIDENTIAL
    assert p.grid_factor == settings.GRID_EMISSIONS_FACTOR_KG_KWH


def test_faithfulness_against_pipeline():
    """The tool's suburb total must match the real Stage 2/3 chain within rounding."""
    df = _synthetic_df()
    base = build_baseline(df)
    rel = crosscheck_against_pipeline(df, base, ghi=MELBOURNE_DEFAULT_GHI_KWH_M2_YR)
    assert rel < 0.01, f"harness drifted {rel:.4%} from the pipeline"


def test_totals_non_negative_and_finite():
    base = build_baseline(_synthetic_df())
    elec, co2 = suburb_totals(base, Params())
    assert elec > 0 and np.isfinite(elec)
    assert co2 > 0 and np.isfinite(co2)


@pytest.mark.parametrize("param,direction", [
    ("grid_factor", +1),       # more CO2 per kWh -> more CO2 saved
    ("cooling_fraction", +1),  # more heat drives cooling -> more saved
    ("ghi", +1),               # more incident energy -> more saved
    ("abs_after", -1),         # higher cool-roof absorptance -> smaller delta -> less
    ("h_outside", -1),         # higher outdoor coeff -> smaller heat fraction -> less
    ("cop_res", -1),           # more efficient AC -> less electricity saved
])
def test_parameter_monotonicity(param, direction):
    """Each knob moves the CO2 total in one unambiguous direction."""
    base = build_baseline(_synthetic_df())
    p_lo = replace(Params(), **{param: getattr(Params(), param) * 0.8})
    p_hi = replace(Params(), **{param: getattr(Params(), param) * 1.2})
    _, co2_lo = suburb_totals(base, p_lo)
    _, co2_hi = suburb_totals(base, p_hi)
    if direction > 0:
        assert co2_hi > co2_lo
    else:
        assert co2_hi < co2_lo


def test_r_roof_and_h_outside_are_coupled():
    """
    Because U_roof << H_OUTSIDE, only the product R_roof x H_OUTSIDE matters.
    Doubling H_OUTSIDE should give ~the same total as doubling r_roof_scale.
    """
    base = build_baseline(_synthetic_df())
    _, co2_double_h = suburb_totals(base, replace(Params(), h_outside=Params().h_outside * 2))
    _, co2_double_r = suburb_totals(base, replace(Params(), r_roof_scale=2.0))
    assert co2_double_h == pytest.approx(co2_double_r, rel=1e-9)


def test_tornado_sorted_by_leverage():
    base = build_baseline(_synthetic_df())
    tornado = oat_tornado(base)
    leverages = [r["leverage_pct"] for r in tornado]
    assert leverages == sorted(leverages, reverse=True)


def test_monte_carlo_brackets_central():
    base = build_baseline(_synthetic_df())
    mc = monte_carlo(base, n_samples=500)
    co2 = mc["co2_saved_kg_yr"]
    assert co2["p5"] < co2["p50"] < co2["p95"]
    assert co2["p5"] < mc["central"]["co2_saved_kg_yr"] < co2["p95"]
