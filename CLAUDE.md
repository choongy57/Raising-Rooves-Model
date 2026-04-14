# Raising Rooves - Cool Roof Intervention Modelling Tool

## Project Identity
Monash University Final Year Project (2026). Builds a data pipeline to model cool roof treatment benefits across Melbourne suburbs.

**Current scope:** Stage 1 (Roof Segmentation) is complete. Stage 2 (Irradiance & Cool Roof Delta) is in progress.

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

# ── Roof pitch extraction (requires Stage 1 output + a DSM GeoTIFF) ──
# High-res DSM (recommended): download from https://elevation.fsdf.org.au/ (ELVIS, 1m)
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif --debug
# Programmatic 30m fallback (set OPENTOPO_API_KEY in .env first):
python -m tools.extract_pitch --suburb Clayton --download-cop30

# ── Stage 2: Cool Roof Delta Calculation ──
python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv
python -m stage2_irradiance.run_stage2 --suburb "Carlton" --irradiance-file data/raw/barra/carlton_ghi.csv --debug

# ── Tests ──
python -m pytest tests/
```

## Stage 1 — Segmentation (COMPLETE)
Uses **OpenStreetMap building footprints** via the Overpass API — no GPU, no API key, no ML.
- One Overpass query per suburb bbox (~15s) returns all building polygons
- Query bbox is tile-extended (~75m buffer) so buildings at suburb edges are included
- Optional merge with VicMap BUILDING_POLYGON.shp via `--merge-footprint-file`
- HSV pixel classifier fills `roof_material`/`roof_colour` when OSM has no tags
- Assumed `pitch_deg` derived from `building_type`, `roof_shape`, and `levels`
- Output: `data/output/stage1_{suburb}.parquet` + `.csv` with columns:
  `suburb, building_id, roof_id, area_m2, lat, lon, source, building_type, levels,`
  `roof_material, roof_colour, roof_shape, pitch_deg, classifier_confidence`
- Annotated visualisation PNG: `data/output/stage1_{suburb}_annotated.png`
- **Local footprint options:**
  - `--footprint-file` — use ONLY a local SHP/GeoJSON (skips OSM)
  - `--merge-footprint-file` — merge local file WITH OSM (OSM primary, local fills gaps)
  - VicMap BUILDING_POLYGON.shp recommended for Melbourne; download via DataShare
- **Legacy files** moved to `stage1_segmentation/_legacy/` — not used in pipeline

## Roof Pitch Extraction (COMPLETE — standalone tool)
`tools/extract_pitch.py` takes Stage 1 output + a DSM GeoTIFF and adds `pitch_deg` per building.
- **Algorithm:** RANSAC (200 iterations, 0.25 m threshold) → SVD refit on inliers
- **Outlier removal:** MAD-based Z-spike filter before fitting (removes chimneys/vents)
- **Outputs:** `stage1_{suburb}_with_pitch.parquet/csv` + `stage1_{suburb}_pitch_map.png`
- **Pitch flags:** `ok` | `flat` (<5°) | `unrealistic` (>65°) | `too_few_points` | `ransac_failed` | `extraction_failed`
- **DSM sources:**
  - 1 m ELVIS (recommended): https://elevation.fsdf.org.au/ — free, registration required
  - 1 m City of Melbourne DSM: https://data.melbourne.vic.gov.au/ — inner suburbs only
  - 30 m COP30 (programmatic fallback): set `OPENTOPO_API_KEY` in `.env`
- **Polygon sidecar:** Stage 1 pipeline now saves `stage1_{suburb}_polygons.json` alongside the parquet; pitch tool reads this for per-building polygon geometry.

## Stage 2 — Cool Roof Delta Calculation (IN PROGRESS)
Joins Stage 1 building data with solar irradiance to compute per-building cool roof benefit.
- **Input:** Stage 1 parquet + irradiance CSV (`lat, lon, annual_ghi_kwh_m2`)
- **Spatial join:** each building matched to nearest irradiance grid cell
- **Physics:** energy saved = GHI × footprint_area × (absorptance_before − 0.20)
  - `absorptance_before` estimated from `roof_colour` (primary) or `roof_material` (fallback)
  - `roof_surface_area_m2` = `area_m2` / cos(pitch_rad) — for materials/cost estimates
- **Output columns (added on top of Stage 1):**
  `annual_ghi_kwh_m2, absorptance_before, roof_surface_area_m2,`
  `energy_saved_kwh_yr, co2_saved_kg_yr`
- Output: `data/output/stage2_{suburb}.parquet` + `.csv`
- **Irradiance data:** provide a CSV with `lat, lon, annual_ghi_kwh_m2` per grid cell
  - BARRA2 via OPeNDAP (`BARRA2_THREDDS_BASE` in settings.py) — connector not yet built
  - Melbourne annual GHI ≈ 1,800–1,900 kWh/m²/yr (use as placeholder if no data yet)

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
- **LiDAR/DSM (1 m):** ELVIS — https://elevation.fsdf.org.au/ (free, registration required)
- **DSM fallback (30 m):** OpenTopography COP30 — key `OPENTOPO_API_KEY` in `.env`
