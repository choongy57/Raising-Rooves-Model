"""
Sensitivity and uncertainty analysis for the Raising Rooves benefit model.

Why this tool exists
--------------------
The per-building cool roof benefit is a *product* of independent factors:

    co2_saved = GHI x footprint_area x (absorptance_before - absorptance_after)
                x [ U_roof / (U_roof + H_OUTSIDE) ] x multistorey            # Stage 3 heat fraction
                x COOLING_FRACTION / COP x grid_factor

Every factor except footprint area is a constant with an *assumed* value
(see config/settings.py). For a multiplicative model, point-validating each
constant one at a time is weak: what matters is (a) each factor's defensible
range and (b) how far the final number moves across those ranges.

This tool therefore does two things on the tracked Carlton sample (no API keys
needed):

  1. One-at-a-time (OAT) tornado: hold every parameter at its central value,
     move one to its literature low then high, record the % change in the
     suburb-total electricity saving. Ranks the parameters by leverage.

  2. Monte Carlo: sample every parameter simultaneously from a triangular
     distribution over its (low, central, high) range and build the output
     distribution (P5 / P50 / P95). This is the honest uncertainty band on the
     headline number.

A structural note the analysis makes concrete: because R_roof ~ 2.5 gives
U_roof ~ 0.4, far below H_OUTSIDE = 25, the heat fraction collapses to
1 / (1 + R_roof x H_OUTSIDE) -- so R_roof and H_OUTSIDE are near-perfectly
coupled and only their *product* matters. The tornado shows this directly.

Faithfulness
------------
The vectorised chain here is cross-checked against the real Stage 2/3 calc
functions (calculate_building_benefit + calculate_thermal_benefit) at default
parameters: the suburb totals must agree to within rounding. If the model
changes and this tool drifts, the cross-check fails loudly.

Usage
-----
    python -m tools.sensitivity_analysis
    python -m tools.sensitivity_analysis --suburb Carlton --samples 20000
    python -m tools.sensitivity_analysis --debug
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")  # headless — write PNGs, never open a window
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.settings import (
    COOL_ROOF_ABSORPTANCE,
    COOLING_FRACTION,
    GRID_EMISSIONS_FACTOR_KG_KWH,
    H_OUTSIDE_W_M2K,
    HVAC_COP_COMMERCIAL,
    HVAC_COP_RESIDENTIAL,
    MELBOURNE_DEFAULT_GHI_KWH_M2_YR,
    MULTISTOREY_ATTENUATION,
    OUTPUT_DIR,
    R_ROOF_BY_CATEGORY,
    R_ROOF_DEFAULT,
    R_ROOF_METAL_RESIDENTIAL,
)
from config.suburbs import get_suburb
from shared.logging_config import setup_logging
from stage2_irradiance.cool_roof_calculator import (
    _absorptance_from_labels,
    calculate_building_benefit,
)
from stage3_thermal.thermal_calculator import (
    _COMMERCIAL_TYPES,
    _METAL_MATERIALS,
    _RESIDENTIAL_TYPES,
    _normalize_label,
    calculate_thermal_benefit,
)

logger = setup_logging("sensitivity_analysis")

SAMPLE_FIXTURE = Path("data/samples/stage1_carlton.parquet")


# ── Parameter vector ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Params:
    """
    The full set of scalar constants the benefit model depends on.

    Defaults mirror config/settings.py exactly, so Params() reproduces the
    shipped model. ``abs_before_bias`` is an additive global shift applied to
    every building's baseline absorptance -- it represents systematic
    classifier / OSM-label error, which has no single constant in settings.py
    but is the largest source of absorptance uncertainty.
    """

    ghi: float = MELBOURNE_DEFAULT_GHI_KWH_M2_YR      # kWh/m2/yr, horizontal
    abs_after: float = COOL_ROOF_ABSORPTANCE          # cool-roof target absorptance
    abs_before_bias: float = 0.0                      # additive shift on baseline absorptance
    h_outside: float = H_OUTSIDE_W_M2K                # W/m2K, outdoor surface coefficient
    r_roof_scale: float = 1.0                         # multiplier on every inferred R_roof
    cooling_fraction: float = COOLING_FRACTION        # fraction of roof heat that drives cooling
    cop_res: float = HVAC_COP_RESIDENTIAL             # residential HVAC COP
    cop_comm: float = HVAC_COP_COMMERCIAL             # commercial HVAC COP
    grid_factor: float = GRID_EMISSIONS_FACTOR_KG_KWH  # kg CO2-e / kWh


# ── Literature ranges (low, central, high) ≈ (P5, P50, P95) ──────────────────
# central == the shipped default. low/high are defensible bounds from the
# sources noted. These are refined by tools/… research; see the findings report
# research/findings/stage3_constant_validation_*.md for citations.

@dataclass(frozen=True)
class Range:
    low: float
    high: float
    source: str


# NOTE: ranges are literature-grounded; see the findings report for citations.
RANGES: dict[str, Range] = {
    "ghi": Range(1700.0, 1950.0, "BoM/NASA POWER Melbourne horizontal GHI spread"),
    "abs_after": Range(0.15, 0.30, "Cool-roof coatings SRI>=78; ages/soils upward over life"),
    "abs_before_bias": Range(-0.10, 0.10, "HSV/OSM classifier systematic error (approx mean per-building 1-sigma)"),
    "h_outside": Range(12.0, 34.0, "AS/NZS 4859 / ISO 6946 external surface Rso 0.03-0.08 -> h=1/Rso"),
    "r_roof_scale": Range(0.5, 1.6, "Uninsulated older stock (R~1) to well-insulated (R~4) vs R2.5 default"),
    "cooling_fraction": Range(0.45, 0.85, "NatHERS/CSIRO share of roof heat gain converting to sensible cooling load"),
    "cop_res": Range(2.5, 4.5, "GEMS MEPS minimum to seasonal best-practice split system"),
    "cop_comm": Range(3.0, 5.5, "AIRAH DA19 commercial VRF/chiller range"),
    "grid_factor": Range(0.55, 0.85, "DCCEEW/AEMO Victorian scope-2 factor, falling trend"),
}


# ── Per-building baseline (fixed attributes) ──────────────────────────────────


@dataclass
class Baseline:
    """Vectorised per-building attributes that do not change between samples."""

    area_m2: np.ndarray            # (N,) footprint area
    abs_before_base: np.ndarray    # (N,) baseline absorptance (pre-bias)
    r_roof_base: np.ndarray        # (N,) inferred R_roof (pre-scale)
    multistorey: np.ndarray        # (N,) 1.0 or MULTISTOREY_ATTENUATION
    is_commercial: np.ndarray      # (N,) bool — selects commercial COP
    n_buildings: int


def _r_roof_base_for(btype: str, roof_material: str | None) -> float:
    """Mirror thermal_calculator._r_roof_for_building (kept local to stay vectorisable)."""
    if btype in _COMMERCIAL_TYPES:
        return R_ROOF_BY_CATEGORY["commercial"]
    if btype in _RESIDENTIAL_TYPES:
        if _normalize_label(roof_material) in _METAL_MATERIALS:
            return R_ROOF_METAL_RESIDENTIAL
        return R_ROOF_BY_CATEGORY["residential"]
    return R_ROOF_DEFAULT


def _baseline_absorptance(row: pd.Series) -> float:
    """
    Mirror the Stage 2 absorptance selection: use the HSV estimate when present
    and positive, else fall back to the OSM colour/material label lookup.
    """
    est = row.get("absorptance_estimate")
    if est is not None and not pd.isna(est) and float(est) > 0:
        return float(est)
    return _absorptance_from_labels(row.get("roof_colour"), row.get("roof_material"))


def build_baseline(df: pd.DataFrame) -> Baseline:
    """Precompute the fixed per-building arrays from a Stage 1 dataframe."""
    area = df["area_m2"].to_numpy(dtype=float)

    abs_before = np.array([_baseline_absorptance(r) for _, r in df.iterrows()], dtype=float)

    btypes = [_normalize_label(v) for v in df["building_type"]]
    r_base = np.array(
        [_r_roof_base_for(bt, rm) for bt, rm in zip(btypes, df["roof_material"])],
        dtype=float,
    )
    is_comm = np.array([bt in _COMMERCIAL_TYPES for bt in btypes], dtype=bool)

    levels = pd.to_numeric(df["levels"], errors="coerce").fillna(1).to_numpy()
    multistorey = np.where(levels >= 4, MULTISTOREY_ATTENUATION, 1.0)

    return Baseline(
        area_m2=area,
        abs_before_base=abs_before,
        r_roof_base=r_base,
        multistorey=multistorey,
        is_commercial=is_comm,
        n_buildings=len(df),
    )


# ── The model chain (vectorised, faithful to Stage 2 + Stage 3) ──────────────


def suburb_totals(base: Baseline, p: Params) -> tuple[float, float]:
    """
    Compute (total_electricity_saved_kwh_yr, total_co2_saved_kg_yr) for the whole
    suburb under parameter set ``p``. Vectorised over all buildings.

    This is the exact Stage 2 -> Stage 3 chain, un-rounded:
        energy_incident   = GHI * area
        energy_saved_abs  = energy_incident * max(0, abs_before - abs_after)
        heat_fraction     = 1 / (1 + R_roof * H_OUTSIDE) * multistorey
        electricity_saved = energy_saved_abs * heat_fraction * cooling_fraction / COP
        co2_saved         = electricity_saved * grid_factor
    """
    abs_before = np.clip(base.abs_before_base + p.abs_before_bias, 0.05, 0.98)
    delta = np.maximum(0.0, abs_before - p.abs_after)

    energy_incident = p.ghi * base.area_m2
    energy_saved_abs = energy_incident * delta

    r_roof = base.r_roof_base * p.r_roof_scale
    # U/(U+H) with U = 1/R  ==  1 / (1 + R*H)
    fraction = 1.0 / (1.0 + r_roof * p.h_outside)
    heat_fraction = fraction * base.multistorey

    cop = np.where(base.is_commercial, p.cop_comm, p.cop_res)
    electricity = energy_saved_abs * heat_fraction * p.cooling_fraction / cop
    co2 = electricity * p.grid_factor

    return float(electricity.sum()), float(co2.sum())


# ── Faithfulness cross-check against the real pipeline functions ──────────────


def crosscheck_against_pipeline(df: pd.DataFrame, base: Baseline, ghi: float) -> float:
    """
    Run the *real* Stage 2 + Stage 3 calc functions per building at default
    parameters and compare the suburb-total electricity to this tool's chain.

    Returns the relative difference. The real functions round intermediate
    values to 1 dp, so an exact match is not expected -- a difference under a
    fraction of a percent proves the harness faithfully mirrors the model.
    """
    total_elec_real = 0.0
    for _, row in df.iterrows():
        s2 = calculate_building_benefit(
            area_m2=float(row["area_m2"]),
            pitch_deg=float(row.get("pitch_deg", 0.0) or 0.0),
            annual_ghi_kwh_m2=ghi,
            roof_colour=row.get("roof_colour"),
            roof_material=row.get("roof_material"),
            absorptance_estimate=row.get("absorptance_estimate"),
            absorptance_uncertainty=row.get("absorptance_uncertainty"),
        )
        s3 = calculate_thermal_benefit(
            energy_saved_kwh_yr=s2["energy_saved_kwh_yr"],
            roof_material=row.get("roof_material"),
            building_type=row.get("building_type"),
            levels=row.get("levels"),
        )
        total_elec_real += s3["electricity_saved_kwh_yr"]

    total_elec_tool, _ = suburb_totals(base, Params(ghi=ghi))
    rel = abs(total_elec_tool - total_elec_real) / total_elec_real if total_elec_real else 0.0
    logger.info(
        "Faithfulness cross-check: pipeline=%.1f kWh/yr  tool=%.1f kWh/yr  rel_diff=%.4f%%",
        total_elec_real, total_elec_tool, rel * 100,
    )
    return rel


# ── One-at-a-time tornado ─────────────────────────────────────────────────────


def oat_tornado(base: Baseline) -> list[dict]:
    """
    For each parameter, hold the rest at central and swing this one low->high.
    Returns a list of dicts sorted by absolute leverage (% change), largest first.
    """
    central = Params()
    _, co2_central = suburb_totals(base, central)

    rows: list[dict] = []
    for name, rng in RANGES.items():
        low_p = replace(central, **{name: rng.low})
        high_p = replace(central, **{name: rng.high})
        _, co2_low = suburb_totals(base, low_p)
        _, co2_high = suburb_totals(base, high_p)
        pct_low = (co2_low - co2_central) / co2_central * 100
        pct_high = (co2_high - co2_central) / co2_central * 100
        rows.append({
            "parameter": name,
            "central_value": getattr(central, name),
            "low": rng.low,
            "high": rng.high,
            "co2_at_low_kg": round(co2_low, 1),
            "co2_at_high_kg": round(co2_high, 1),
            "pct_change_low": round(pct_low, 1),
            "pct_change_high": round(pct_high, 1),
            "leverage_pct": round(max(abs(pct_low), abs(pct_high)), 1),
            "source": rng.source,
        })

    rows.sort(key=lambda r: r["leverage_pct"], reverse=True)
    return rows


# ── Monte Carlo ───────────────────────────────────────────────────────────────


def monte_carlo(base: Baseline, n_samples: int, seed: int = 42) -> dict:
    """
    Sample every parameter from a triangular(low, central, high) distribution
    and build the suburb-total CO2 / electricity distribution.
    """
    rng = np.random.default_rng(seed)
    central = Params()

    def draw(name: str) -> np.ndarray:
        r = RANGES[name]
        mode = getattr(central, name)
        # Guard against triangular() requiring low <= mode <= high
        lo, hi = min(r.low, mode), max(r.high, mode)
        return rng.triangular(lo, mode, hi, size=n_samples)

    samples = {name: draw(name) for name in RANGES}

    co2 = np.empty(n_samples)
    elec = np.empty(n_samples)
    for i in range(n_samples):
        p = Params(**{name: float(samples[name][i]) for name in RANGES})
        elec[i], co2[i] = suburb_totals(base, p)

    _, co2_central = suburb_totals(base, central)
    elec_central, _ = suburb_totals(base, central)

    def pct(a: np.ndarray, q: float) -> float:
        return float(np.percentile(a, q))

    return {
        "n_samples": n_samples,
        "central": {
            "electricity_saved_kwh_yr": round(elec_central, 1),
            "co2_saved_kg_yr": round(co2_central, 1),
        },
        "co2_saved_kg_yr": {
            "p5": round(pct(co2, 5), 1),
            "p50": round(pct(co2, 50), 1),
            "p95": round(pct(co2, 95), 1),
            "mean": round(float(co2.mean()), 1),
            "cov": round(float(co2.std() / co2.mean()), 3) if co2.mean() else None,
        },
        "electricity_saved_kwh_yr": {
            "p5": round(pct(elec, 5), 1),
            "p50": round(pct(elec, 50), 1),
            "p95": round(pct(elec, 95), 1),
            "mean": round(float(elec.mean()), 1),
        },
        "_co2_samples": co2,  # kept for plotting; stripped before JSON dump
    }


# ── Plotting ──────────────────────────────────────────────────────────────────


def plot_tornado(tornado: list[dict], out_path: Path) -> None:
    names = [r["parameter"] for r in tornado][::-1]
    lows = [r["pct_change_low"] for r in tornado][::-1]
    highs = [r["pct_change_high"] for r in tornado][::-1]

    fig, ax = plt.subplots(figsize=(9, 0.55 * len(names) + 1.5))
    y = np.arange(len(names))
    for i, (lo, hi) in enumerate(zip(lows, highs)):
        left, right = sorted((lo, hi))
        ax.barh(i, right - left, left=left, color="#4C82C3", edgecolor="black", height=0.6)
    ax.axvline(0, color="black", linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.set_xlabel("% change in suburb-total CO2 saved (vs central)")
    ax.set_title("Sensitivity tornado — one parameter varied low↔high")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def plot_distribution(mc: dict, out_path: Path) -> None:
    co2 = mc["_co2_samples"]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(co2, bins=60, color="#4C82C3", edgecolor="white")
    for q, style, label in [(5, "--", "P5"), (50, "-", "P50"), (95, "--", "P95")]:
        v = float(np.percentile(co2, q))
        ax.axvline(v, color="black", linestyle=style, linewidth=1.2)
        ax.text(v, ax.get_ylim()[1] * 0.92, f" {label}", rotation=90, va="top", fontsize=8)
    ax.set_xlabel("Suburb-total CO2 saved (kg/yr)")
    ax.set_ylabel("Monte Carlo frequency")
    ax.set_title(f"Output uncertainty — {mc['n_samples']} samples over literature ranges")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


# ── Orchestration ─────────────────────────────────────────────────────────────


def load_sample(suburb_name: str) -> pd.DataFrame:
    """
    Load the Stage 1 dataframe for a suburb. Prefers a real Stage 1 output in
    data/output/, else falls back to the tracked Carlton sample fixture.
    """
    suburb = get_suburb(suburb_name)
    candidate = OUTPUT_DIR / f"stage1_{suburb.key}.parquet"
    if candidate.exists():
        logger.info("Using Stage 1 output: %s", candidate)
        return pd.read_parquet(candidate)
    if suburb.key == "carlton" and SAMPLE_FIXTURE.exists():
        logger.info("Using tracked sample fixture: %s", SAMPLE_FIXTURE)
        return pd.read_parquet(SAMPLE_FIXTURE)
    raise FileNotFoundError(
        f"No Stage 1 parquet for {suburb_name}. Run Stage 1 first, or use --suburb Carlton."
    )


def run(suburb_name: str, n_samples: int) -> dict:
    df = load_sample(suburb_name)
    logger.info("Loaded %d buildings for %s.", len(df), suburb_name)

    base = build_baseline(df)

    rel = crosscheck_against_pipeline(df, base, ghi=MELBOURNE_DEFAULT_GHI_KWH_M2_YR)
    if rel > 0.01:
        logger.warning(
            "Cross-check drift %.3f%% exceeds 1%% — harness may no longer mirror the pipeline.",
            rel * 100,
        )

    tornado = oat_tornado(base)
    logger.info("Tornado (by leverage): %s",
                ", ".join(f"{r['parameter']}=±{r['leverage_pct']}%" for r in tornado))

    mc = monte_carlo(base, n_samples)
    logger.info(
        "Monte Carlo CO2: P5=%.0f  P50=%.0f  P95=%.0f kg/yr  (central %.0f, CoV %.2f)",
        mc["co2_saved_kg_yr"]["p5"], mc["co2_saved_kg_yr"]["p50"],
        mc["co2_saved_kg_yr"]["p95"], mc["central"]["co2_saved_kg_yr"],
        mc["co2_saved_kg_yr"]["cov"] or 0.0,
    )

    suburb = get_suburb(suburb_name)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tornado_png = OUTPUT_DIR / f"sensitivity_{suburb.key}_tornado.png"
    dist_png = OUTPUT_DIR / f"sensitivity_{suburb.key}_distribution.png"
    json_path = OUTPUT_DIR / f"sensitivity_{suburb.key}.json"

    plot_tornado(tornado, tornado_png)
    plot_distribution(mc, dist_png)

    mc_json = {k: v for k, v in mc.items() if k != "_co2_samples"}
    result = {
        "suburb": suburb.name,
        "n_buildings": base.n_buildings,
        "crosscheck_rel_diff_pct": round(rel * 100, 4),
        "tornado": tornado,
        "monte_carlo": mc_json,
    }
    with open(json_path, "w") as fh:
        json.dump(result, fh, indent=2)

    logger.info("Wrote %s, %s, %s", json_path.name, tornado_png.name, dist_png.name)
    print(f"\nOutputs written to {OUTPUT_DIR}:")
    for p in (json_path, tornado_png, dist_png):
        print(f"  {p.name}")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Sensitivity + uncertainty analysis of the cool-roof benefit constants.",
    )
    parser.add_argument("--suburb", default="Carlton",
                        help="Suburb to analyse (default: Carlton, uses tracked sample).")
    parser.add_argument("--samples", type=int, default=10000,
                        help="Monte Carlo sample count (default: 10000).")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    if args.debug:
        logger.setLevel("DEBUG")

    logger.info("=== Sensitivity Analysis: %s ===", args.suburb)
    try:
        run(args.suburb, args.samples)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
