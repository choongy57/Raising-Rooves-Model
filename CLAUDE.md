# Raising Rooves - Claude Project Guide

This file is the working memory and operating guide for Claude in the
Raising Rooves Model repository. Use it together with `README.md`, which is
the public project guide.

## Project Snapshot

Raising Rooves is a Monash University Final Year Project for modelling the
benefits of cool roof interventions across Melbourne suburbs.

- Team: Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle
- Supervisor: Stuart
- Current phase: Stage 1 roof segmentation is complete; Stage 2 irradiance
  and cool roof delta calculation is in progress
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

### In Progress

- Stage 2 cool roof delta calculation.
- Per-building join between Stage 1 outputs and irradiance data.
- Cool roof physics calculation:
  `energy_saved = GHI * footprint_area * (absorptance_before - 0.20)`.
- Support for user-provided irradiance CSV files.
- BARRA2/ERA5 climate-data path is present but still needs reliable access and
  integration validation.

### Next Priorities

1. Replace assumed roof pitch with measured pitch from 1 m DSM where available.
2. Connect real annual GHI data from BARRA2 or a prepared irradiance CSV.
3. Add Stage 3 thermal modelling to translate absorbed solar reduction into
   estimated building cooling electricity savings.
4. Improve roof material classification and validate against local statistics.
5. Expand suburb coverage and keep suburb metadata tied to ABS SA2 where
   practical.

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

### MVP Coordinate Analysis

```bash
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185
python -m tools.analyse_coordinate --suburb Clayton
python -m tools.analyse_coordinate --suburb Clayton --radius 500
python -m tools.analyse_coordinate --suburb Clayton --grid 5
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --footprint-file data/raw/footprints/australia.geojson
```

### Stage 1 - Roof Segmentation

```bash
python -m stage1_segmentation.run_stage1 --suburb "Richmond"
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --debug
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --max-tiles 10
python -m stage1_segmentation.run_stage1 --list-suburbs
```

### Roof Pitch Extraction

```bash
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif --debug
python -m tools.extract_pitch --suburb Clayton --download-cop30
```

### Stage 2 - Cool Roof Delta

```bash
python -m stage2_irradiance.run_stage2 --suburb "Carlton"
python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv
python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv --debug
```

### Tests

```bash
python -m pytest tests/
```

### QA Ticket Monitor

```bash
python -m tools.test_monitor                 # run tests, auto-create tickets
python -m tools.test_monitor --dry-run       # parse failures only, no sheet writes
python -m tools.test_monitor --triage-only   # re-triage all open tickets
python -m tools.test_monitor --list          # print open tickets to console
python -m tools.test_monitor --debug
```

## External APIs And Data Sources

Use this as the quick checklist when Ryan asks "what APIs/data do we use?"

| Purpose | Source | Access | Current use |
| --- | --- | --- | --- |
| Satellite imagery | Google Maps Static API | `GOOGLE_MAPS_API_KEY` in `.env` | Active tile download |
| Building footprints | OpenStreetMap Overpass API | No key | Active Stage 1 footprint source |
| Building footprints supplement | VicMap BUILDING_POLYGON | Manual SHP download from DataShare | Optional merge |
| Irradiance | BARRA2 via NCI THREDDS/OPeNDAP | NCI/data access required | Intended Stage 2 source |
| Irradiance fallback | ERA5 / CDS | `CDS_API_KEY` if used | Fallback path in code |
| DSM pitch data | ELVIS 1 m LiDAR | Manual download, free registration | Recommended pitch source |
| DSM inner-city fallback | City of Melbourne Open Data DSM | Manual download | Useful for inner suburbs |
| DSM coarse fallback | OpenTopography COP30 | `OPENTOPO_API_KEY` in `.env` | Programmatic fallback |
| Suburb boundaries | ABS SA2 shapefiles / manual bbox | Manual data prep | Needed for robust coverage |
| QA ticket tracker | Google Sheets | `GOOGLE_SHEET_ID` + `GWS_CREDS_FILE` in `.env` | Active — `tools/test_monitor.py` |

Never paste API keys into notes, commits, chat, or screenshots.

## Stage Notes

### Stage 1 - Complete

Stage 1 uses OSM building footprints, optionally merged with VicMap building
polygons. It classifies roof material and colour with an HSV pixel classifier
when roof tags are missing. It writes:

- `data/output/stage1_{suburb}.parquet`
- `data/output/stage1_{suburb}.csv`
- `data/output/stage1_{suburb}_annotated.png`
- `data/output/stage1_{suburb}_polygons.json`

Important columns include:

`suburb, building_id, roof_id, area_m2, lat, lon, source, building_type,
levels, roof_material, roof_colour, roof_shape, pitch_deg,
classifier_confidence`

### Roof Pitch Extraction - Complete Standalone Tool

`tools/extract_pitch.py` adds measured pitch where a DSM GeoTIFF is available.

- Algorithm: RANSAC plane fit, then SVD refit on inliers.
- Outlier removal: MAD-based Z-spike filter.
- Outputs: `stage1_{suburb}_with_pitch.parquet/csv` and
  `stage1_{suburb}_pitch_map.png`.
- Flags: `ok`, `flat`, `unrealistic`, `too_few_points`, `ransac_failed`,
  `extraction_failed`.

### Stage 2 - In Progress

Stage 2 joins Stage 1 buildings to annual irradiance and computes per-building
cool roof benefit.

Input expectations:

- Stage 1 parquet for the suburb.
- Optional irradiance CSV with `lat, lon, annual_ghi_kwh_m2`.

Added output columns:

`annual_ghi_kwh_m2, absorptance_before, roof_surface_area_m2,
energy_saved_kwh_yr, co2_saved_kg_yr`

Important limitation:

`energy_saved_kwh_yr` currently means reduced absorbed solar energy, not
building electricity savings. Stage 3 should handle thermal transfer and HVAC
efficiency.

## QA Ticket Workflow

Tickets live in the `Tickets` tab of the Google Sheet at:
`https://docs.google.com/spreadsheets/d/1z_eGmxD2i_fewjbLDBB36IMgFKzJ3WyDaQdipnD03_8`

Auth uses `GWS_CREDS_FILE` (the existing `uni-email.json` OAuth2 credential —
same account as the GWS MCP server). No separate service account is needed.

### Ticket lifecycle

`open` → `triaged` → `in_progress` → `review` → `closed`

Auto-triage (via `tools/triage_agent.py`) assigns:

| Field | How assigned |
| --- | --- |
| `stage` | regex match on title/description against module names |
| `type` | regex match for test_failure / data_quality / logic_bug / performance / config |
| `priority` | P1 for physics/unit bugs; P2 for test failures; P3 for missing data; P4 for perf |

### Priority rules (P1 = most urgent)

| Priority | Trigger |
| --- | --- |
| P1-critical | Physics/unit code: `energy_saved`, `absorptance`, `kWh`, `W/m2`, `epsg` |
| P2-high | Any pytest `FAILED`/`ERROR`, pipeline crash |
| P3-medium | Missing data, fallback triggered, NaN values |
| P4-low | Performance, config, cosmetic |

### When to run the monitor

- Run `python -m tools.test_monitor` before committing any physics or data-join changes.
- Use `--dry-run` to preview without touching the sheet.
- Duplicate detection: an identical title with status `open/triaged/in_progress` won't create a second ticket.

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
4. Suggested vault notes:
   - `Projects/Raising Rooves/Overview.md`
   - `Projects/Raising Rooves/Current Plan.md`
   - `Projects/Raising Rooves/API and Data Sources.md`
   - `Projects/Raising Rooves/Decisions.md`
   - `Projects/Raising Rooves/Research Questions.md`
   - `Projects/Raising Rooves/Meeting Notes.md`
5. Add backlinks from project notes to relevant FYP or university notes if
   those notes already exist.
6. Do not move secrets, raw datasets, huge outputs, or generated tiles into the
   vault.
7. When a decision changes code behaviour, update both the repo docs and the
   relevant vault planning note.

Suggested wiki structure:

```text
Projects/Raising Rooves/
  Overview.md
  Current Plan.md
  API and Data Sources.md
  Architecture.md
  Decisions.md
  Research Questions.md
  Meeting Notes.md
  Stage 1 - Roof Segmentation.md
  Stage 2 - Irradiance and Cool Roof Delta.md
  Stage 3 - Thermal Modelling.md
```

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
