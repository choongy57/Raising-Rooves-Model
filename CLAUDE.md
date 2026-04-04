# Raising Rooves - Cool Roof Intervention Modelling Tool

## Project Identity
Monash University Final Year Project (2026). Builds a data pipeline to model cool roof treatment benefits across Melbourne suburbs.

**Current scope:** Stage 1 (Roof Segmentation) is complete. Stage 2 (Irradiance & Climate Data) is next.

**Team:** Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle. **Supervisor:** Stuart.

## Architecture Rules
- Each pipeline stage is independently runnable via `python -m stageN.run_stageN`
- **No database** — all persistence is CSV or Parquet in `data/`
- All API keys loaded from `.env` via `python-dotenv` — never hardcode secrets
- Use `logging` module (not print) — configured via `shared/logging_config.py`
- Type hints on all function signatures
- Each module function should be self-contained and testable in isolation

## Data Conventions
- Coordinates: EPSG:4326 (WGS84 lat/lon)
- Suburb identification: ABS SA2 codes where possible
- Tile naming: `{suburb}_{zoom}_{x}_{y}.png`
- Area units: square metres (m²)
- Irradiance: W/m² (instantaneous) or kWh/m²/day (daily)
- Temperature: degrees Celsius

## File Conventions
- Python files: `snake_case.py`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE` in `config/settings.py`

## Run Commands
```bash
# ── MVP: Analyse a single coordinate ──
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185
python -m tools.analyse_coordinate --suburb Clayton
python -m tools.analyse_coordinate --suburb Clayton --radius 500   # 500m radius
python -m tools.analyse_coordinate --suburb Clayton --grid 5       # explicit 5x5 grid
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug
# Optional: local MS Building Footprints (~845MB download):
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --footprint-file data/raw/footprints/australia.geojson

# ── Stage 1: Full suburb roof segmentation ──
python -m stage1_segmentation.run_stage1 --suburb "Richmond"
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --debug
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --max-tiles 10  # smoke test
python -m stage1_segmentation.run_stage1 --list-suburbs                      # list available suburbs

# ── Stage 2: Irradiance & Climate Data (not yet implemented) ──
# python -m stage2_irradiance.run_stage2 --suburb "Richmond"

# ── Tests ──
python -m pytest tests/
```

## Stage 1 — Segmentation (COMPLETE)
Uses **OpenStreetMap building footprints** via the Overpass API — no GPU, no API key, no ML.
- One Overpass query per suburb bbox (~15s) returns all building polygons
- Returns polygon vertices (lat/lon), area (m²), OSM building ID per building
- Satellite tiles (512×512px, zoom 19, TILE_STEP=2) stitch seamlessly — zero seam
- Output: `data/output/stage1_{suburb}.parquet` with columns: suburb, building_id, roof_id, area_m2, lat, lon, source
- **Local alternative:** `--footprint-file` → Microsoft Australia Building Footprints GeoJSON
  - Download: https://github.com/microsoft/AustraliaBuildingFootprints (~845 MB zipped)
  - Better outer-suburb coverage; same ODbL license
- **Legacy files** (not used in pipeline, kept for reference): `gemini_segmenter.py`, `solar_api_segmenter.py`, `sam_segmenter.py`

## Stage 1 — Known Gaps (next steps)
1. ~~**OSM roof tags**~~ — DONE. `roof:material`, `roof:colour`, `roof:shape`, `building:levels`, `building_type` now extracted and included in output.
2. **Roof material fallback** — `roof_classifier.py` exists (HSV-based pixel classifier) but is NOT wired into the pipeline. Should call it when OSM has no `roof:material` tag.
3. **Building type coverage** — outer suburbs have lower OSM tag coverage; consider Overture Maps if gaps become an issue.

## Stage 2 — Irradiance & Climate Data (NOT STARTED)
- Pull solar irradiance from BARRA2 (BOM, 4km resolution) via OPeNDAP
- ERA5 as fallback
- Match per-suburb building data from Stage 1 to nearest climate grid cell
- Output: irradiance + temperature per suburb per time period

## Git Workflow
- **`git add` + `git commit`** only — do NOT `git push` unless Ryan explicitly asks
- Commit after each meaningful unit of work (one feature, one fix)
- Write commit messages that explain *why*, not just *what*

## README Updates
Update `README.md` when:
- A new feature is added to a CLI tool (new flag, new output column, etc.)
- A new pipeline stage is started or completed
- A known limitation is resolved
- Do NOT update README for internal refactors, comment fixes, or minor cleanups

## Debugging
- All CLI entry points accept `--debug` to set logging to DEBUG level
- Logs output to both console and `logs/{module}_{date}.log`
- MVP tool saves annotated PNG + CSV to `data/output/`

## Research Skill
When asked to research a topic (e.g., "research roof segmentation datasets"):
1. Use web search to find 5-10 relevant sources
2. Create a markdown summary in `research/findings/{topic_slug}_{date}.md`
3. Include: source URLs, key findings, relevance to project, recommended next steps
4. Focus on: datasets, pretrained models, API access methods, benchmarks

## Key Data Sources
- **Satellite imagery:** Google Maps Static API (key in `.env`)
- **Roof materials:** CSR dataset, CSIRO VIC stats (~45-50% metal, ~30% tile)
- **Climate:** BARRA2 (BOM, 4km resolution) via OPeNDAP, ERA5 fallback
- **Suburb boundaries:** ABS SA2 shapefiles
