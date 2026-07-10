# Unified Architecture Proposal
**Generated:** 2026-05-01

## Fixes ranked by impact

---

### Fix 1 — Move Stage 3 physics constants to config ⭐ HIGHEST IMPACT

**Why:** FYP requires a sensitivity analysis. You can't run one if the constants are buried
in thermal_calculator.py. Examiners will ask "what if COP = 2.5 instead of 3.0?"

**Change:** Move these from `thermal_calculator.py:20-38` to `config/settings.py`:
```python
HEAT_TRANSFER_FRACTION_LOW_RISE   = 0.65   # AS 4859.1 roof assembly
HEAT_TRANSFER_FRACTION_HIGH_RISE  = 0.40   # 4+ storey thermal mass attenuation
COOLING_FRACTION                  = 0.70   # NatHERS 6-star Melbourne model
HVAC_COP_RESIDENTIAL              = 3.0    # GEMS Determination 2019 minimum
HVAC_COP_COMMERCIAL               = 4.0    # AIRAH DA19 commercial baseline
```
`thermal_calculator.py` imports them from settings; code is unchanged.

---

### Fix 2 — Fix monthly W/m² → kWh/m²/day conversion ⭐ ACTUAL BUG

**Why:** `irradiance_processor.py:57` multiplies by 24 (hours/day) to get daily energy.
But BARRA2 `rsds` is a mean flux over all hours including night — multiplying by 24 is
correct for converting mean W/m² to Wh/m²/day. **This is actually fine.** However, if
the value is a daytime-only mean (some sources), you'd need ~12. Add a comment to
clarify the assumption, and prefer `compute_annual_ghi_from_hourly()` which is unambiguous.

**Change:** Ensure Stage 2 always uses `compute_annual_ghi_from_hourly()` when BARRA2
data is available (already the preferred path at `pipeline.py:108`). Add an explicit
WARNING log if the monthly path is taken.

---

### Fix 3 — Validate user CSV irradiance units ⭐ SILENT BUG

**Why:** If a user provides kWh/m²/day instead of kWh/m²/yr, Stage 2 silently produces
results 365× too small. Melbourne is ~1,650 kWh/m²/yr = ~4.5 kWh/m²/day — a sanity
check catches this.

**Change:** In `irradiance_loader.py:56` after loading CSV, add:
```python
if irradiance_df["annual_ghi_kwh_m2"].max() < 100:
    logger.warning("annual_ghi_kwh_m2 max=%.1f looks like daily values (kWh/m²/day). "
                   "Expected kWh/m²/yr (Melbourne ~1650). Check your CSV units.")
```

---

### Fix 4 — Add Suburb.key property ⭐ QUICK WIN

**Why:** `suburb.name.lower().replace(" ", "_")` is inlined 7 times. One typo creates
a file mismatch silently.

**Change:** Add to `config/suburbs.py` Suburb dataclass:
```python
@property
def key(self) -> str:
    return self.name.lower().replace(" ", "_")
```
Then replace all 7 inline normalisations. No logic changes.

---

### Fix 5 — Consolidate stage save/load boilerplate

**Why:** 12 lines duplicated × 3 = 36 lines of boilerplate. If OUTPUT_DIR changes, all 3
stages need updating.

**New function** in `shared/file_io.py`:
```python
def save_stage_outputs(df: pd.DataFrame, stage: int, suburb_key: str) -> tuple[Path, Path]:
    parquet_path = OUTPUT_DIR / f"stage{stage}_{suburb_key}.parquet"
    csv_path     = OUTPUT_DIR / f"stage{stage}_{suburb_key}.csv"
    ensure_dir(OUTPUT_DIR)
    save_parquet(df, parquet_path)
    df.to_csv(csv_path, index=False)
    return parquet_path, csv_path

def load_stage_input(stage: int, suburb_key: str) -> pd.DataFrame | None:
    path = OUTPUT_DIR / f"stage{stage}_{suburb_key}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)
```

---

### Fix 6 — Either use absorptance_uncertainty or remove it

**Why:** The column exists in Stage 1 output, is passed through to Stage 2, but never
used in any calculation. This is misleading — it implies uncertainty is propagated when
it isn't.

**Options:**
- **Remove it** from Stage 1 output columns if there's no plan to use it
- **Use it** in a simple output annotation: flag buildings where `absorptance_uncertainty > 0.12` as `absorptance_confidence = "low"` in Stage 2 output

Recommendation: keep the column but add the confidence flag — it costs nothing and
strengthens the FYP model.

---

## What NOT to change

- The GHI fallback chain logic — it's appropriate complexity for the 4-source problem
- Stage 2 and Stage 3 summary statistics — different physical quantities, keep separate
- The HSV classifier threshold rules — empirical, changing them needs validation data first

---

## Unified Data Flow (current vs proposed)

```mermaid
flowchart LR
    S1["Stage 1\nrun_stage1.py"] -->|stage1_{key}.parquet| S2
    S2["Stage 2\nrun_stage2.py"] -->|stage2_{key}.parquet| S3
    S3["Stage 3\nrun_stage3.py"] -->|stage3_{key}.parquet| VIS

    subgraph shared["shared/ (proposed additions)"]
        FIO["file_io.save_stage_outputs()\nfile_io.load_stage_input()"]
        KEY["Suburb.key property"]
        FLOAT["utils.safe_float()"]
    end

    subgraph config["config/ (proposed additions)"]
        PHYS["settings.py:\nHEAT_TRANSFER_FRACTION\nCOOLING_FRACTION\nHVAC_COP_*"]
    end

    S1 --> FIO
    S2 --> FIO
    S3 --> FIO
    S2 --> FIO
    S3 --> FIO
    S1 --> KEY
    S2 --> KEY
    S3 --> KEY
    S3 --> PHYS
```
