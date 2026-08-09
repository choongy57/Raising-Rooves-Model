# Raising Rooves - Claude Project Guide

This file is the working memory and operating guide for Claude in the
Raising Rooves Model repository. Use it together with `README.md`, which is
the public project guide.

## Project Snapshot

Raising Rooves is a Monash University Final Year Project for modelling the
benefits of cool roof interventions across Melbourne suburbs.

- Team: Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle
- Supervisor: Stuart
- Current phase: Stages 1-3 all run end-to-end. Focus has shifted from
  building the pipeline to validating it (Stage 3 constants, HSV classifier,
  suburb boundaries) for the final FYP report
- Persistence model: no database; store outputs as CSV, Parquet, JSON, and
  images under `data/`
- Main goal: produce defensible per-building estimates of cool roof benefit,
  then improve the model enough for final FYP reporting

## Start Every Session Here

1. Read `README.md` for the current user-facing state of the project.
2. Read this file for agent workflow, project plan, and memory rules.
3. Check the worktree before editing:
   ```bash
   git status --short
   ```
4. Inspect the relevant module before proposing or changing code.
5. Preserve user work. Do not revert unrelated files or delete generated data
   unless Ryan explicitly asks.
6. When the task affects project scope, outputs, CLI flags, or known
   limitations, update `README.md`.

## Current Project Plan

### Completed

- Stage 1 roof segmentation using OpenStreetMap building footprints via
  Overpass API.
- Optional VicMap building polygon merge.
- HSV roof material and colour classifier for missing OSM roof tags.
- Annotated Stage 1 visualisation PNG output.
- Standalone roof pitch extraction tool using DSM GeoTIFF input.
- Stage 1 polygon sidecar JSON for per-building pitch extraction.

### Completed (recently)

- Stage 2 cool roof delta calculation with per-building irradiance join.
  Physics: `energy_saved = GHI * footprint_area * (absorptance_before - 0.20)`.
  Irradiance priority: BARRA2 (dormant, needs NCI) → user CSV → NASA POWER
  (de-facto source, keyless, cached) → Melbourne default constant. Output
  carries an `irradiance_source` column. The ERA5/CDS fallback was removed.
- Stage 3 thermal modelling: per-building R_roof inferred from building
  attributes → heat-transfer fraction `U/(U+h_out)` → cooling load →
  electricity saved → CO2. See `DECISION_LOG.md` for the design rationale.
- Tracked sample fixture `data/samples/stage1_carlton.parquet` so a fresh
  clone runs Stages 2-3 with no API keys.

### Next Priorities (ranked — keep in sync with README Roadmap)

1. Validate Stage 3 constants (`H_OUTSIDE`, `COOLING_FRACTION`, COP, R_roof
   proxy table) against Stuart's NatHERS runs / AS-NZS 4859.1, and publish a
   sensitivity analysis. All constants live in `config/settings.py`.
2. Validate the HSV classifier by running the Gemini experiment
   (`tools.run_gemini_osm_experiment`) on a stratified sample of 150-300
   buildings per suburb and reporting agreement rates.
3. Replace rectangular bboxes with true ABS SA2 suburb polygons and an
   `inside_suburb` flag; report in-boundary totals.
4. Wire measured DSM pitch into Stage 2 (it currently writes an orphaned
   `stage1_{suburb}_with_pitch.parquet` no stage reads), or formally de-scope
   it — pitch only affects roof-area/costing, not energy numbers.
5. Expand to 3+ suburbs and use `tools.compare_suburbs` for FYP reporting.
6. Connect real BARRA2 GHI when NCI project ob53 access is available
   (flip `BARRA2_ENABLED` in `config/settings.py`).
7. Move remaining in-module constants (absorptance tables in
   `cool_roof_calculator.py`, assumed-pitch table in Stage 1 pipeline) into
   `config/settings.py` for sensitivity sweeps.

## Architecture Rules

- Each pipeline stage must be independently runnable via:
  `python -m stageN_module.run_stageN`
- Do not introduce a database without an explicit project decision.
- Load all API keys from `.env` through `python-dotenv`.
- Never hardcode secrets, tokens, API keys, or private paths.
- Use the `logging` module, not `print`, for application logging.
- Logging should use `shared/logging_config.py` where possible.
- Type-hint every function signature.
- Keep module functions self-contained and testable in isolation.
- Prefer existing shared utilities over new helper code.
- Keep outputs in `data/output/` unless the user asks for a different path.

## Data Conventions

- Coordinates: EPSG:4326 / WGS84 latitude and longitude.
- Suburb identification: use ABS SA2 codes where possible.
- Tile naming: `{suburb}_{zoom}_{x}_{y}.png`.
- Area: square metres; prefer `m2` in agent docs and code comments unless
  user-facing docs already use another safe convention.
- Irradiance: W/m2 for instantaneous values; kWh/m2/day or kWh/m2/year for
  aggregated values.
- Temperature: degrees Celsius.

## File Conventions

- Python files: `snake_case.py`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`, usually in `config/settings.py`
- Tests: place focused tests under `tests/`
- Research notes: place sourced findings under `research/findings/`

## Codebase Check Workflow

When asked to understand or change the project:

1. Identify the pipeline stage involved.
2. Read the CLI entry point first.
3. Follow the orchestrator/pipeline file.
4. Read the domain modules used by that stage.
5. Check tests and existing sample outputs.
6. Confirm whether the change affects output columns, CLI flags, or README
   claims.
7. Run the narrowest useful verification command.

Useful inspection commands:

```bash
rg --files
rg -n "def |class |argparse|click|typer|annual_ghi|pitch_deg" .
python -m pytest tests/
```

## Run Commands

All `python -m ...` entry points are documented in `README.md` — run `--help`
on any entry point for the full flag list. The canonical invocation per stage:

```bash
python -m stage1_segmentation.run_stage1 --suburb "<name>"
python -m stage2_irradiance.run_stage2 --suburb "<name>"
python -m stage3_thermal.run_stage3 --suburb "<name>"
python -m tools.visualise_results --suburb <name>
python -m tools.compare_suburbs
python -m pytest tests/
```

## External APIs And Data Sources

Use this as the quick checklist when Ryan asks "what APIs/data do we use?"

| Purpose | Source | Access | Current use |
| --- | --- | --- | --- |
| Satellite imagery | Google Maps Static API | `GOOGLE_MAPS_API_KEY` in `.env` | Active tile download |
| Building footprints | OpenStreetMap Overpass API | No key | Active Stage 1 footprint source |
| Building footprints supplement | VicMap BUILDING_POLYGON | Manual SHP download from DataShare | Optional merge |
| Irradiance (active) | NASA POWER REST API | No key; cached under `data/raw/nasa_power/` | De-facto Stage 2 source |
| Irradiance (future) | BARRA2 via NCI THREDDS/OPeNDAP | NCI project ob53 access required | Dormant until access lands |
| DSM pitch data | ELVIS 1 m LiDAR | Manual download, free registration | Recommended pitch source |
| DSM inner-city fallback | City of Melbourne Open Data DSM | Manual download | Useful for inner suburbs |
| DSM coarse fallback | OpenTopography COP30 | `OPENTOPO_API_KEY` in `.env` | Programmatic fallback |
| Suburb boundaries | ABS SA2 shapefiles / manual bbox | Manual data prep | Needed for robust coverage |
| QA ticket tracker | Google Sheets | `GOOGLE_SHEET_ID` + `GWS_CREDS_FILE` in `.env` | Active — `tools/test_monitor.py` |

Never paste API keys into notes, commits, chat, or screenshots.

## Stage Notes & QA

Per-stage detail (output columns, physics limitations, QA ticket workflow) is in
the lazy-loaded `stage-notes` skill — invoke it when working on a pipeline stage.
Quick reference:

- **Stage 1:** OSM + VicMap footprints, HSV pixel classifier. `energy_saved_kwh_yr`
  is absorbed solar reduction — NOT electricity savings. Stage 3 handles that.
- **Stage 3:** R_roof inferred per building. Constants in `config/settings.py`
  are unvalidated Melbourne defaults — the #1 roadmap item.
- **QA:** `python -m tools.test_monitor` before physics/data-join changes. Tickets
  in Google Sheets, priority: P1=physics bugs, P2=test failures.

## README Update Rules

Update `README.md` when:

- A CLI flag is added, removed, or changed.
- Output files or output columns change.
- A pipeline stage is started, completed, or materially redesigned.
- A known limitation is resolved or a new important limitation is discovered.
- Setup steps, API keys, or data-source requirements change.

Do not update `README.md` for internal-only refactors or comment cleanup.

## Research Workflow

When Ryan asks to research a topic:

1. Use web search and collect 5-10 relevant sources.
2. Write a markdown summary to:
   `research/findings/{topic_slug}_{YYYY-MM-DD}.md`
3. Include source URLs, key findings, relevance to Raising Rooves, and
   recommended next steps.
4. Focus on datasets, pretrained models, API access, benchmarks, and practical
   integration cost.
5. Note uncertainty clearly when sources are weak, stale, or not Melbourne
   specific.

## ChoongyOS Vault / Personal Brain Workflow

Ryan wants FYP planning and decisions linked into his personal knowledge base,
referred to as "ChoongyOS Vault".

Preferred vault location, if available:

```text
C:\Users\choon\ChoongyOS Vault
```

If that path is not accessible, ask Ryan for the exact vault path before
writing outside this repository.

When asked to sync project knowledge into the vault:

1. Keep repo docs as the source of truth for runnable code instructions.
2. Keep the vault as the source of truth for study notes, planning decisions,
   meeting notes, research summaries, and FYP wiki pages.
3. Create or update a Raising Rooves area in the vault, preferably:
   ```text
   Projects/Raising Rooves/
   ```
4. Suggested vault notes (see step 3 for preferred directory):
   - `Overview.md`, `Current Plan.md`, `API and Data Sources.md`
   - `Decisions.md`, `Research Questions.md`, `Meeting Notes.md`
   - One per pipeline stage (e.g. `Stage 1 - Roof Segmentation.md`)
5. Add backlinks from project notes to relevant FYP or university notes if
   those notes already exist.
6. Do not move secrets, raw datasets, huge outputs, or generated tiles into the
   vault.
7. When a decision changes code behaviour, update both the repo docs and the
   relevant vault planning note.

Suggested decision entry format:

```markdown
## YYYY-MM-DD - Decision title

Decision:

Why:

Tradeoffs:

Code/docs affected:

Follow-up:
```

## Git Workflow

- Use `git add` and `git commit` only.
- Do not push unless Ryan explicitly asks.
- Commit after each meaningful unit of work.
- Commit messages should explain why the change exists, not only what changed.
- Before committing, check:
  ```bash
  git status --short
  ```

## Debugging Rules

- All CLI entry points should accept `--debug`.
- Debug mode should set logging to DEBUG.
- Logs should write to console and `logs/{module}_{date}.log`.
- MVP and pipeline tools should save useful outputs to `data/output/`.
- Prefer narrow reproducible commands in bug reports.

## Quality Bar

- Keep changes small and traceable.
- Prefer tests around physics, data joins, coordinate logic, and CLI argument
  behaviour.
- Avoid silent fallbacks for scientific calculations; log assumptions and mark
  output columns clearly.
- Treat unit confusion as a serious bug. Check W/m2 versus kWh/m2/year and
  footprint area versus roof surface area.
- Treat CRS confusion as a serious bug. Confirm EPSG:4326 inputs before spatial
  joins or distance calculations.
