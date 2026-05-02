"""
Multi-suburb comparison tool for the Raising Rooves pipeline.

Reads all available Stage 3 outputs (falling back to Stage 2) across configured
suburbs and produces three comparison artefacts:

  1. data/output/suburb_comparison.csv   — per-suburb summary table
  2. data/output/suburb_comparison.png   — 3-panel comparison bar charts
  3. data/output/suburb_comparison.html  — HTML table + embedded chart

Useful for FYP reporting: shows which suburbs benefit most and why.

Usage:
    python -m tools.compare_suburbs
    python -m tools.compare_suburbs --stage 2    # force Stage 2 data
    python -m tools.compare_suburbs --debug
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config.settings import GRID_EMISSIONS_FACTOR_KG_KWH, OUTPUT_DIR
from config.suburbs import SUBURBS
from shared.logging_config import setup_logging

logger = setup_logging("compare_suburbs")

_HOUSEHOLD_KWH_YR = 4_200  # AER 2023, Victorian residential average


# ── Data loading ──────────────────────────────────────────────────────────────


def _load_suburb(suburb_key: str, force_stage: int | None = None) -> tuple[pd.DataFrame | None, int]:
    """
    Load the best available output for a suburb.

    Returns (df, stage_used) where stage_used is 3 or 2.
    Returns (None, 0) if no output exists.
    """
    if force_stage != 2:
        p3 = OUTPUT_DIR / f"stage3_{suburb_key}.parquet"
        if p3.exists():
            return pd.read_parquet(p3), 3

    p2 = OUTPUT_DIR / f"stage2_{suburb_key}.parquet"
    if p2.exists():
        return pd.read_parquet(p2), 2

    return None, 0


def build_comparison_table(force_stage: int | None = None) -> pd.DataFrame:
    """
    Aggregate per-building Stage 3/2 outputs to suburb-level summary rows.

    Returns a DataFrame with one row per suburb that has output data.
    """
    rows = []
    for key, suburb in SUBURBS.items():
        df, stage = _load_suburb(key, force_stage)
        if df is None:
            logger.debug("No output for %s — skipping.", suburb.name)
            continue

        n = len(df)
        total_roof_area = float(df.get("roof_surface_area_m2", df["area_m2"]).sum())
        mean_ghi = float(df["annual_ghi_kwh_m2"].mean()) if "annual_ghi_kwh_m2" in df.columns else 0.0
        mean_absorptance = float(df["absorptance_before"].mean()) if "absorptance_before" in df.columns else 0.0
        total_absorbed = float(df["energy_saved_kwh_yr"].sum()) if "energy_saved_kwh_yr" in df.columns else 0.0

        if stage == 3 and "electricity_saved_kwh_yr" in df.columns:
            total_elec = float(df["electricity_saved_kwh_yr"].sum())
            total_co2 = float(df["co2_electricity_saved_kg_yr"].sum())
        else:
            # Approximate from absorbed solar using AEMO factor (Stage 2 only)
            total_elec = total_absorbed * 0.65 * 0.70 / 3.0
            total_co2 = total_elec * GRID_EMISSIONS_FACTOR_KG_KWH

        rows.append({
            "suburb": suburb.name,
            "zone_type": suburb.zone_type,
            "data_stage": stage,
            "n_buildings": n,
            "total_roof_area_m2": round(total_roof_area, 0),
            "mean_annual_ghi_kwh_m2": round(mean_ghi, 1),
            "mean_absorptance_before": round(mean_absorptance, 3),
            "total_absorbed_solar_kwh_yr": round(total_absorbed, 0),
            "total_electricity_saved_kwh_yr": round(total_elec, 0),
            "total_co2_saved_kg_yr": round(total_co2, 0),
            "elec_per_m2_kwh_yr": round(total_elec / total_roof_area, 3) if total_roof_area > 0 else 0.0,
            "equiv_households": round(total_elec / _HOUSEHOLD_KWH_YR, 1),
        })

    if not rows:
        logger.warning("No suburb outputs found in %s.", OUTPUT_DIR)
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("total_electricity_saved_kwh_yr", ascending=False)
    return df.reset_index(drop=True)


# ── Charts ────────────────────────────────────────────────────────────────────


def _bar_colours(n: int, cmap: str = "tab10") -> list:
    return list(plt.get_cmap(cmap)(np.linspace(0, 0.85, n)))


def build_comparison_charts(df: pd.DataFrame) -> Path:
    """
    Build a 3-panel horizontal bar chart PNG comparing all suburbs.

    Panels:
      1. Total electricity saved (GWh/yr) — sorted
      2. Electricity saved per m² of roof area (kWh/yr/m²) — intensity metric
      3. CO₂ avoided (tonnes/yr) — sorted same order as panel 1
    """
    suburbs = df["suburb"].tolist()
    n = len(suburbs)
    colours = _bar_colours(n)

    fig, axes = plt.subplots(1, 3, figsize=(16, max(4, n * 0.55 + 1.5)))
    fig.suptitle(
        f"Raising Rooves — Suburb Comparison  ({datetime.now().strftime('%d %b %Y')})",
        fontsize=13, fontweight="bold",
    )

    def _hbar(ax, values, title, xlabel, unit_scale=1.0, fmt="{:,.1f}"):
        scaled = [v * unit_scale for v in values]
        bars = ax.barh(suburbs, scaled, color=colours, edgecolor="white", linewidth=0.5)
        ax.set_title(title, fontsize=10, fontweight="semibold")
        ax.set_xlabel(xlabel, fontsize=9)
        ax.tick_params(axis="y", labelsize=9)
        ax.tick_params(axis="x", labelsize=8)
        ax.invert_yaxis()
        for bar, val in zip(bars, scaled):
            w = bar.get_width()
            ax.text(w * 1.01, bar.get_y() + bar.get_height() / 2,
                    fmt.format(val), va="center", ha="left", fontsize=8)
        ax.margins(x=0.18)

    _hbar(
        axes[0],
        df["total_electricity_saved_kwh_yr"].tolist(),
        "Electricity Saved",
        "GWh / yr",
        unit_scale=1e-6,
        fmt="{:,.3f}",
    )
    _hbar(
        axes[1],
        df["elec_per_m2_kwh_yr"].tolist(),
        "Electricity Saved per m² Roof",
        "kWh / yr / m²",
        unit_scale=1.0,
        fmt="{:,.2f}",
    )
    _hbar(
        axes[2],
        df["total_co2_saved_kg_yr"].tolist(),
        "CO₂ Avoided",
        "tonnes CO₂ / yr",
        unit_scale=1e-3,
        fmt="{:,.1f}",
    )

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out_path = OUTPUT_DIR / "suburb_comparison.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    logger.info("Comparison chart saved to %s", out_path)
    return out_path


# ── HTML report ───────────────────────────────────────────────────────────────


def build_comparison_html(df: pd.DataFrame, chart_path: Path) -> Path:
    """Build a single-page HTML comparison report with table + embedded chart."""
    run_date = datetime.now().strftime("%d %B %Y")
    chart_rel = chart_path.name

    total_elec = df["total_electricity_saved_kwh_yr"].sum()
    total_co2 = df["total_co2_saved_kg_yr"].sum()
    total_buildings = df["n_buildings"].sum()
    total_equiv = total_elec / _HOUSEHOLD_KWH_YR

    def _row(r) -> str:
        stage_badge = (
            f'<span style="font-size:0.75rem;color:#2d6a9f;font-weight:600">S{r.data_stage}</span>'
        )
        return (
            f"<tr>"
            f"<td>{r.suburb} {stage_badge}</td>"
            f"<td>{r.zone_type}</td>"
            f"<td>{r.n_buildings:,}</td>"
            f"<td>{r.total_roof_area_m2 / 1e4:,.1f}</td>"
            f"<td>{r.mean_annual_ghi_kwh_m2:,.0f}</td>"
            f"<td>{r.mean_absorptance_before:.3f}</td>"
            f"<td>{r.total_electricity_saved_kwh_yr / 1e6:,.4f}</td>"
            f"<td>{r.total_co2_saved_kg_yr / 1000:,.1f}</td>"
            f"<td>{r.equiv_households:,.1f}</td>"
            f"<td>{r.elec_per_m2_kwh_yr:.3f}</td>"
            f"</tr>"
        )

    table_rows = "\n".join(_row(r) for r in df.itertuples())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Raising Rooves — Suburb Comparison</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f7f9fc; color: #1a2332;
    }}
    header {{
      background: linear-gradient(135deg, #1a3a5c 0%, #2d6a9f 100%);
      color: white; padding: 28px 40px 20px;
    }}
    header h1 {{ font-size: 1.7rem; font-weight: 700; }}
    header p  {{ margin-top: 6px; opacity: 0.85; font-size: 0.9rem; }}
    .container {{ max-width: 1100px; margin: 0 auto; padding: 32px 24px; }}
    .kpi-row {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px; margin-bottom: 28px;
    }}
    .kpi {{
      background: white; border-radius: 10px; padding: 18px 20px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.07); border-left: 4px solid #2d6a9f;
    }}
    .kpi .value {{ font-size: 1.5rem; font-weight: 700; color: #2d6a9f; line-height: 1.1; }}
    .kpi .label {{ font-size: 0.75rem; color: #6b7a8d; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .section-title {{
      font-size: 1rem; font-weight: 600; color: #1a3a5c;
      margin: 24px 0 10px; padding-bottom: 6px; border-bottom: 2px solid #e2eaf3;
    }}
    .chart-wrap {{
      background: white; border-radius: 10px; padding: 14px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.07); text-align: center;
    }}
    .chart-wrap img {{ max-width: 100%; height: auto; border-radius: 6px; }}
    table {{
      width: 100%; border-collapse: collapse; background: white;
      border-radius: 10px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.07);
      font-size: 0.87rem;
    }}
    th {{
      background: #1a3a5c; color: white; padding: 10px 12px;
      text-align: left; font-weight: 600; font-size: 0.8rem;
    }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #e8edf3; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:nth-child(even) {{ background: #f7f9fc; }}
    .note {{
      font-size: 0.75rem; color: #8a95a3; margin-top: 24px;
      border-top: 1px solid #e2eaf3; padding-top: 12px;
    }}
  </style>
</head>
<body>
  <header>
    <h1>Raising Rooves — Suburb Comparison</h1>
    <p>Cool roof benefit analysis across {len(df)} suburbs &nbsp;|&nbsp; Generated {run_date}</p>
  </header>
  <div class="container">

    <div class="kpi-row">
      <div class="kpi">
        <div class="value">{len(df)}</div>
        <div class="label">Suburbs compared</div>
      </div>
      <div class="kpi">
        <div class="value">{total_buildings:,}</div>
        <div class="label">Total buildings</div>
      </div>
      <div class="kpi">
        <div class="value">{total_elec / 1e6:,.3f} GWh/yr</div>
        <div class="label">Total electricity saved</div>
      </div>
      <div class="kpi">
        <div class="value">{total_co2 / 1e6:,.2f} kt CO₂/yr</div>
        <div class="label">CO₂ avoided</div>
      </div>
      <div class="kpi">
        <div class="value">{total_equiv:,.0f}</div>
        <div class="label">Equiv. households powered</div>
      </div>
    </div>

    <div class="section-title">Comparison Charts</div>
    <div class="chart-wrap">
      <img src="{chart_rel}" alt="Suburb comparison charts" />
    </div>

    <div class="section-title">Suburb Summary Table</div>
    <table>
      <thead>
        <tr>
          <th>Suburb</th>
          <th>Zone</th>
          <th>Buildings</th>
          <th>Roof area (ha)</th>
          <th>GHI (kWh/m²/yr)</th>
          <th>Mean absorptance</th>
          <th>Elec saved (GWh/yr)</th>
          <th>CO₂ (t/yr)</th>
          <th>Equiv HH</th>
          <th>kWh/yr/m²</th>
        </tr>
      </thead>
      <tbody>
{table_rows}
      </tbody>
    </table>

    <p class="note">
      <strong>Methodology:</strong>
      Electricity saved = absorbed solar reduction (Stage 2) × 0.65 heat transfer × 0.70 cooling fraction / COP (3.0 res / 4.0 comm).
      Absorptance estimated from HSV pixel classification of satellite imagery (AS/NZS 4859.1).
      CO₂ factor: Victorian grid 0.79 kg CO₂-e/kWh (AEMO 2023). Equiv. households: {_HOUSEHOLD_KWH_YR:,} kWh/yr (AER 2023).
      S2 = Stage 2 data only (Stage 3 not yet run); S3 = Stage 3 thermal model applied.
    </p>

  </div>
</body>
</html>
"""

    out_path = OUTPUT_DIR / "suburb_comparison.html"
    out_path.write_text(html, encoding="utf-8")
    logger.info("Comparison HTML saved to %s", out_path)
    return out_path


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Compare cool roof benefit across all suburbs with available output data.",
    )
    parser.add_argument(
        "--stage",
        type=int,
        choices=[2, 3],
        default=None,
        help="Force Stage 2 or Stage 3 data. Default: use best available per suburb.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    if args.debug:
        logger.setLevel("DEBUG")

    logger.info("=== Compare Suburbs ===")

    df = build_comparison_table(force_stage=args.stage)
    if df.empty:
        logger.error(
            "No suburb outputs found in %s. "
            "Run Stage 2 or 3 for at least one suburb first.",
            OUTPUT_DIR,
        )
        sys.exit(1)

    logger.info("Loaded data for %d suburbs.", len(df))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUTPUT_DIR / "suburb_comparison.csv"
    df.to_csv(csv_path, index=False)
    logger.info("Summary table saved to %s", csv_path)

    chart_path = build_comparison_charts(df)
    html_path = build_comparison_html(df, chart_path)

    print(f"\nOutputs written to {OUTPUT_DIR}:")
    print(f"  {csv_path.name}")
    print(f"  {chart_path.name}")
    print(f"  {html_path.name}")


if __name__ == "__main__":
    main()
