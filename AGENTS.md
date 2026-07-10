# Raising Rooves - Agent Operating Guide

This repository is the Raising Rooves Model for a Monash University Final Year
Project. It builds a data pipeline for modelling cool roof treatment benefits
across Melbourne suburbs.

Use this file as the canonical guide for coding agents. Use `CLAUDE.md` for the
Claude-specific project memory and ChoongyOS Vault workflow. Use `README.md`
for user-facing setup, commands, and project explanation.

## Project State

- Stage 1 roof segmentation: complete.
- Roof pitch extraction from DSM: complete as a standalone tool.
- Stage 2 irradiance and cool roof delta: in progress.
- Stage 3 thermal modelling: planned.
- Persistence: no database; CSV, Parquet, JSON, and images under `data/`.
- Team: Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle.
- Supervisor: Stuart.

## First Steps For Every Task

1. Read `README.md` for the current public project description.
2. Read this file for agent rules.
3. Check the worktree:
   ```bash
   git status --short
   ```
4. Inspect before editing. Start from the CLI entry point, then the pipeline,
   then the domain modules.
5. Preserve user changes. Do not revert unrelated edits or delete generated
   outputs unless Ryan explicitly asks.
6. Update `README.md` only when public behaviour, commands, outputs, setup, or
   known limitations change.

## Architecture Rules

- Each pipeline stage must be independently runnable with `python -m ...`.
- No database unless Ryan explicitly approves an architecture change.
- All API keys must come from `.env` through `python-dotenv`.
- Never hardcode secrets.
- Use `logging`, not `print`, for pipeline/application messages.
- Reuse `shared/logging_config.py` for logging setup.
- Type-hint all function signatures.
- Keep functions self-contained and testable.
- Prefer existing repo patterns and helpers over new abstractions.
- Keep generated outputs in `data/output/` unless a tool already has a
  documented output path.

## Data Conventions

- Coordinates: EPSG:4326 / WGS84 latitude and longitude.
- Suburbs: use ABS SA2 codes where possible.
- Tile names: `{suburb}_{zoom}_{x}_{y}.png`.
- Area: square metres.
- Irradiance: W/m2 for instantaneous values; kWh/m2/day or kWh/m2/year for
  aggregate values.
- Temperature: degrees Celsius.
- Be explicit about footprint area versus roof surface area.

## File Conventions

- Python files: `snake_case.py`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Config constants: prefer `config/settings.py`
- Research summaries: `research/findings/{topic_slug}_{YYYY-MM-DD}.md`
- Tests: `tests/`

## Current Plan

### Done

- OSM building footprint ingestion through Overpass API.
- Optional VicMap BUILDING_POLYGON merge.
- HSV roof material/colour classification.
- Stage 1 CSV, Parquet, polygon sidecar, and annotated PNG outputs.
- DSM-based roof pitch extraction tool.

### In Progress

- Stage 2 annual irradiance join.
- Cool roof delta calculation.
- Validation of BARRA2/ERA5 climate-data ingestion.
- Cleaner output interpretation for policy/reporting use.

### Next

1. Use measured pitch wherever DSM coverage exists.
2. Connect real annual GHI through BARRA2 or prepared CSVs.
3. Add Stage 3 thermal modelling for cooling electricity savings.
4. Improve material classification accuracy.
5. Expand and validate suburb coverage.

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

### Stage 1

```bash
python -m stage1_segmentation.run_stage1 --suburb "Richmond"
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --debug
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --max-tiles 10
python -m stage1_segmentation.run_stage1 --list-suburbs
```

### Pitch Extraction

```bash
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif --debug
python -m tools.extract_pitch --suburb Clayton --download-cop30
```

### Stage 2

```bash
python -m stage2_irradiance.run_stage2 --suburb "Carlton"
python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv
python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv --debug
```

### Tests

```bash
python -m pytest tests/
```

## API And Data Inventory

| Purpose | Source | Access | Status |
| --- | --- | --- | --- |
| Satellite imagery | Google Maps Static API | `GOOGLE_MAPS_API_KEY` in `.env` | Active |
| Building footprints | OpenStreetMap Overpass API | No key | Active |
| Building footprints supplement | VicMap BUILDING_POLYGON | Manual SHP download | Optional |
| Irradiance | BARRA2 via NCI THREDDS/OPeNDAP | NCI/data access required | Intended |
| Irradiance fallback | ERA5 / CDS | `CDS_API_KEY` if used | Fallback |
| DSM pitch | ELVIS 1 m LiDAR | Manual download | Recommended |
| DSM inner-city pitch | City of Melbourne Open Data DSM | Manual download | Useful for inner suburbs |
| DSM coarse fallback | OpenTopography COP30 | `OPENTOPO_API_KEY` in `.env` | Optional fallback |
| Suburb boundaries | ABS SA2 shapefiles/manual bbox | Manual prep | Needed for expansion |

## Stage Summaries

### Stage 1 - Roof Segmentation

Input: suburb name and optional local footprint file.

Process:

- Download Google Maps satellite tiles.
- Query OSM building footprints using Overpass.
- Optionally merge VicMap footprints.
- Classify roof material and colour.
- Estimate fallback pitch from building type, roof shape, and levels.

Outputs:

- `data/output/stage1_{suburb}.parquet`
- `data/output/stage1_{suburb}.csv`
- `data/output/stage1_{suburb}_annotated.png`
- `data/output/stage1_{suburb}_polygons.json`

### Roof Pitch Extraction

Input: Stage 1 output plus DSM GeoTIFF.

Process:

- Extract DSM points inside each building polygon.
- Remove Z-spike outliers.
- Fit dominant roof plane with RANSAC.
- Refit inliers with SVD.
- Save pitch, aspect, RMSE, point counts, and flags.

Outputs:

- `data/output/stage1_{suburb}_with_pitch.parquet`
- `data/output/stage1_{suburb}_with_pitch.csv`
- `data/output/stage1_{suburb}_pitch_map.png`

### Stage 2 - Cool Roof Delta

Input: Stage 1 parquet and optional irradiance CSV.

Process:

- Match each building to nearest irradiance grid cell.
- Estimate pre-treatment absorptance from roof colour/material.
- Calculate roof surface area from pitch.
- Calculate reduced absorbed solar energy and CO2 avoided.

Important limitation:

`energy_saved_kwh_yr` is currently reduced absorbed solar energy, not reduced
electricity demand. Stage 3 must model roof heat transfer and HVAC efficiency.

## README Update Rules

Update `README.md` when:

- A new CLI flag is added or an existing flag changes.
- Output files or columns change.
- A pipeline stage starts, completes, or changes scope.
- Setup instructions, dependencies, API keys, or data sources change.
- A known limitation is resolved or a major new limitation is discovered.

Do not update `README.md` for internal-only refactors, formatting-only changes,
or small comments.

## Research Workflow

When asked to research a topic:

1. Search for 5-10 relevant sources.
2. Write a markdown summary under `research/findings/`.
3. Include source URLs, key findings, relevance to this project, and
   recommended next steps.
4. Focus on datasets, pretrained models, API access methods, benchmarks, and
   integration cost.
5. Make uncertainty explicit.

## ChoongyOS Vault Coordination

Ryan's personal brain is called "ChoongyOS Vault". Do not assume it is writable
unless the exact path is available and inside the allowed workspace.

Expected path if Ryan confirms it:

```text
C:\Users\choon\ChoongyOS Vault
```

When asked to update the vault:

- Keep repo docs focused on runnable code and reproducibility.
- Keep vault notes focused on project planning, decisions, meeting notes,
  research interpretation, and FYP wiki pages.
- Suggested vault folder: `Projects/Raising Rooves/`.
- Suggested notes:
  - `Overview.md`
  - `Current Plan.md`
  - `API and Data Sources.md`
  - `Architecture.md`
  - `Decisions.md`
  - `Research Questions.md`
  - `Meeting Notes.md`
- Do not put secrets, raw datasets, downloaded tiles, large zips, or generated
  outputs into the vault.
- When code behaviour changes because of a decision, update both repo docs and
  the relevant vault decision/planning note.

## Git Workflow

- Use `git add` and `git commit` only.
- Do not push unless Ryan explicitly asks.
- Commit after each meaningful unit of work.
- Write commit messages that explain why the change exists.
- Check `git status --short` before staging or committing.

## Quality Checks

- Run the narrowest useful test command before finishing.
- For scientific calculations, check units carefully.
- For spatial logic, check CRS and distance assumptions.
- For output schema changes, update docs and tests.
- For network/API code, handle missing credentials and failed requests clearly.
- For fallbacks, log the assumption and make the output interpretation obvious.
