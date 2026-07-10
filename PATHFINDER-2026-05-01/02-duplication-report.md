# Duplication Report
**Generated:** 2026-05-01

## Cross-Feature Duplications (worth consolidating)

### D1 — Stage output save pattern (12 lines × 3 stages)
Every pipeline writes outputs the same way:
```python
parquet_path = OUTPUT_DIR / f"stage{N}_{suburb_key}.parquet"
csv_path     = OUTPUT_DIR / f"stage{N}_{suburb_key}.csv"
ensure_dir(OUTPUT_DIR)
save_parquet(df, parquet_path)
df.to_csv(csv_path, index=False)
logger.info("Saved %s", parquet_path)
```
**Locations:** `stage1_segmentation/pipeline.py:479`, `stage2_irradiance/pipeline.py:310`, `stage3_thermal/pipeline.py:120`
**Verdict:** Consolidate into `shared/file_io.save_stage_outputs(df, stage_num, suburb_key)`

---

### D2 — Load-previous-stage pattern
Stage 2 and 3 both load their upstream parquet identically (path build → exists check → error log → read).
**Locations:** `stage2_irradiance/pipeline.py:187`, `stage3_thermal/pipeline.py:61`
**Verdict:** Consolidate into `shared/file_io.load_stage_input(stage_num, suburb_key)`

---

### D3 — Suburb key normalisation repeated 7 times
`suburb.name.lower().replace(" ", "_")` is inlined everywhere.
**Locations:** `stage1_segmentation/pipeline.py:398`, `tile_downloader.py:115`, `stage1_visualiser.py:251`, `stage2_irradiance/pipeline.py:69,180`, `stage3_thermal/pipeline.py:54`, `tools/visualise_results.py:641`
**Verdict:** Add `Suburb.key` property to `config/suburbs.py` dataclass — one definition, used everywhere.

---

### D4 — NaN/None float coercion inconsistency
Stage 1 checks `is not None`; Stage 2 additionally checks `str(est) != "nan"`. Different guards for the same data flow.
**Locations:** `stage1_segmentation/pipeline.py:299`, `stage2_irradiance/pipeline.py:286`
**Verdict:** Add `shared/utils.safe_float(value)` used at both call sites.

---

## Within-Feature Duplications (worth fixing locally)

### D5 — Centroid calculation inlined 3× in Stage 1
`sum(lats)/len(lats)` logic at `pipeline.py:129`, `pipeline.py:281`, `pipeline.py:67`.
**Verdict:** Extract to `shared/geo_utils.polygon_centroid(coords)`.

---

### D6 — Per-building row iteration inconsistent between Stage 2 and 3
Stage 2 Step 2 uses plain `iterrows` without tqdm; Stage 2 Step 3 and Stage 3 use `tqdm(df.iterrows(), ...)`.
**Locations:** `stage2_irradiance/pipeline.py:261` (no progress), `pipeline.py:277` and `stage3_thermal/pipeline.py:74` (with progress).
**Verdict:** Add tqdm to the Step 2 loop for consistency; low priority.

---

## Legitimate Specialisations (do NOT consolidate)

| Pattern | Why it differs | Decision |
|---|---|---|
| Stage 2 vs Stage 3 summary stats | Different physical quantities (energy vs electricity vs CO2) | Keep separate |
| BARRA2 vs NASA POWER vs CSV fallback chain | Different sources with different auth, format, and resolution | Keep as is |
| Stage 1 classifier confidence vs OSM confidence | Different semantics — classifier uncertainty vs trusted tag | Keep, but document |

---

## Dead Code / Unused Parameters (bugs, not duplication)

| Issue | Location | Risk |
|---|---|---|
| `absorptance_uncertainty` computed in Stage 1, passed to Stage 2, **never used in any calculation** | `stage2_irradiance/pipeline.py:287`, `cool_roof_calculator.py:67` | Dead data; misleads FYP reader into thinking uncertainty is propagated |
| `roof_material` passed to `calculate_thermal_benefit()` but ignored inside | `stage3_thermal/pipeline.py:77`, `thermal_calculator.py:78` | Dead parameter |
| Monthly W/m² → kWh/m²/day conversion uses factor **24** instead of ~**12** effective sun hours | `stage2_irradiance/irradiance_processor.py:57` | Overestimates GHI by ~2× if monthly path is used |
| NASA POWER input CSV units not validated — silent 365× error if user passes kWh/m²/day | `stage2_irradiance/irradiance_loader.py:56` | Silent wrong results |
| Stage 3 physics constants (HEAT_TRANSFER_FRACTION, COOLING_FRACTION, HVAC_COP) hard-coded in thermal_calculator.py, not in config/settings.py | `stage3_thermal/thermal_calculator.py:20-38` | Hard to run FYP sensitivity analysis |
