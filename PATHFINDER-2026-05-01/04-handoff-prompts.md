# Handoff Prompts
**Generated:** 2026-05-01

Copy any of these directly into a prompt to implement the fix.

---

## Fix 1 — Stage 3 physics constants to config

```
Move Stage 3 physics constants from stage3_thermal/thermal_calculator.py to config/settings.py.

Constants to move (currently at thermal_calculator.py:20-38):
  HEAT_TRANSFER_FRACTION_LOW_RISE = 0.65
  HEAT_TRANSFER_FRACTION_HIGH_RISE = 0.40
  COOLING_FRACTION = 0.70
  HVAC_COP_RESIDENTIAL = 3.0
  HVAC_COP_COMMERCIAL = 4.0

Steps:
1. Add the five constants to config/settings.py with their existing docstring citations
2. In thermal_calculator.py, delete the local definitions and add an import from config.settings
3. No logic changes — only moving the definitions
4. Run pytest to confirm all tests pass
5. Do not add a feature flag or backwards-compat shim — just move them

Anti-patterns to avoid:
- Do not add a config class or dataclass — plain module-level constants are fine
- Do not change the values
```

---

## Fix 2 — Validate user CSV irradiance units

```
Add a sanity check for user-provided irradiance CSV units in stage2_irradiance/irradiance_loader.py.

Problem: if a user passes kWh/m²/day instead of kWh/m²/yr, results are 365× too small.
Melbourne annual GHI is ~1650 kWh/m²/yr = ~4.5 kWh/m²/day, so any max < 100 is suspicious.

Change to make (at irradiance_loader.py:56, after loading and validating columns):
  if irradiance_df["annual_ghi_kwh_m2"].max() < 100:
      logger.warning(
          "annual_ghi_kwh_m2 max value is %.1f — this looks like daily values "
          "(kWh/m²/day). Pipeline expects annual kWh/m²/yr (Melbourne ~1650). "
          "Check your CSV units.",
          irradiance_df["annual_ghi_kwh_m2"].max(),
      )

Also add a check for implausibly high values:
  if irradiance_df["annual_ghi_kwh_m2"].max() > 4000:
      logger.warning("annual_ghi_kwh_m2 max=%.1f exceeds realistic range (max ~2500 for Australia)",
                     irradiance_df["annual_ghi_kwh_m2"].max())

Add a test: tests/test_irradiance_loader.py — test that a CSV with daily values triggers the warning.
Run pytest to confirm all tests pass.
```

---

## Fix 3 — Suburb.key property

```
Add a `key` property to the Suburb dataclass in config/suburbs.py, then replace all 7 inline
normalisations across the codebase.

Step 1: In config/suburbs.py, add to the Suburb dataclass:
  @property
  def key(self) -> str:
      return self.name.lower().replace(" ", "_")

Step 2: Replace all 7 inline occurrences of suburb_name.lower().replace(" ", "_") or
suburb.name.lower().replace(" ", "_") with suburb.key or suburb_name_key (where suburb
is the Suburb object). Locations:
  - stage1_segmentation/pipeline.py:398
  - stage1_segmentation/tile_downloader.py:115
  - stage1_segmentation/stage1_visualiser.py:251
  - stage2_irradiance/pipeline.py:69,180
  - stage3_thermal/pipeline.py:54
  - tools/visualise_results.py:641

Note: some call sites only have the suburb name string, not the Suburb object. For those,
keep the inline normalisation but add a comment pointing to Suburb.key as the canonical form.

Run pytest to confirm all tests pass. No logic changes.
```

---

## Fix 4 — absorptance_uncertainty confidence flag

```
The column absorptance_uncertainty exists in Stage 1 output and is passed to Stage 2,
but is never used in any calculation. Either use it or make the unused state explicit.

Recommended: add an absorptance_confidence column to Stage 2 output.

In stage2_irradiance/pipeline.py, after the benefit calculation loop (around line 292),
add a column:
  df["absorptance_confidence"] = df["absorptance_uncertainty"].apply(
      lambda u: "low" if (u is not None and not pd.isna(u) and float(u) > 0.12) else "ok"
  )

This flags buildings where the HSV classifier was uncertain (chromatic surfaces or
mid-brightness grey). The column is informational only — it doesn't change the calculation.

Add the column name to the CLAUDE.md Stage 2 output columns list.
Run pytest to confirm all tests pass.
```

---

## Fix 5 — Consolidate stage save/load boilerplate

```
Extract the repeated save-outputs and load-input patterns into shared/file_io.py.

Add two functions to shared/file_io.py:

def save_stage_outputs(df: pd.DataFrame, stage: int, suburb_key: str) -> tuple[Path, Path]:
    parquet_path = OUTPUT_DIR / f"stage{stage}_{suburb_key}.parquet"
    csv_path     = OUTPUT_DIR / f"stage{stage}_{suburb_key}.csv"
    ensure_dir(OUTPUT_DIR)
    save_parquet(df, parquet_path)
    df.to_csv(csv_path, index=False)
    logger.info("Saved %s and %s", parquet_path.name, csv_path.name)
    return parquet_path, csv_path

def load_stage_input(stage: int, suburb_key: str) -> pd.DataFrame | None:
    path = OUTPUT_DIR / f"stage{stage}_{suburb_key}.parquet"
    if not path.exists():
        logger.error("Stage %d output not found: %s — run stage %d first", stage, path, stage)
        return None
    return pd.read_parquet(path)

Then replace the 3 save blocks and 2 load blocks:
  - stage1_segmentation/pipeline.py:479-487 → save_stage_outputs(df, 1, suburb_key)
  - stage2_irradiance/pipeline.py:310-316  → save_stage_outputs(df, 2, suburb_key)
  - stage3_thermal/pipeline.py:120-126     → save_stage_outputs(df, 3, suburb_key)
  - stage2_irradiance/pipeline.py:187-194  → load_stage_input(1, suburb_key)
  - stage3_thermal/pipeline.py:61-68       → load_stage_input(2, suburb_key)

Run pytest to confirm all tests pass.
Anti-patterns: do not break stage1 climate parquet path (stage2_irradiance/pipeline.py:145) — that uses a different suffix (_climate) and should not use save_stage_outputs.
```
