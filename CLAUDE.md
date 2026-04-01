# Raising Rooves - Cool Roof Intervention Modelling Tool

## Project Identity
Monash University Final Year Project (2026). Builds a data pipeline to model cool roof treatment benefits across Melbourne suburbs.

**Current scope:** Stage 1 (Roof Segmentation) and Stage 2 (Irradiance & Climate Data) only.

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
# ── MVP: Analyse a single coordinate (OSM building footprints, no key needed) ──
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185
python -m tools.analyse_coordinate --suburb Clayton
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug
# With local MS Building Footprints file (optional, ~845MB download):
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --footprint-file data/raw/footprints/australia.geojson

# ── Stage 1: Full suburb roof segmentation ──
python -m stage1_segmentation.run_stage1 --suburb "Richmond"
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --debug
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --max-tiles 10  # smoke test

# ── Stage 2: Irradiance & Climate Data ──
python -m stage2_irradiance.run_stage2 --suburb "Richmond"
python -m stage2_irradiance.run_stage2 --suburb "Richmond" --debug

# ── Tests ──
python -m pytest tests/
```

## Segmentation Approach
Stage 1 uses **OpenStreetMap building footprints** via the Overpass API — no GPU, no API key, no large download.
- `building_footprint_segmenter.py` queries OSM Overpass API for all buildings in a tile bounding box
- Returns actual polygon vertices (lat/lon), measured area (m²), and OSM building ID per building
- Polygons projected to tile pixel space via Mercator math; overlaid on satellite imagery
- Response time: ~15 seconds per tile bbox (Overpass server processing)
- **Local alternative:** Pass `--footprint-file` pointing at a local Microsoft Australia Building Footprints GeoJSON
  - Download from: https://github.com/microsoft/AustraliaBuildingFootprints (~845 MB zipped, covers all Melbourne)
  - Faster repeated queries; same ODbL license

The legacy Gemini Vision segmenter (`gemini_segmenter.py`) is retained for reference.

## Debugging
- All CLI entry points accept `--debug` to set logging to DEBUG level
- Logs output to both console and `logs/{module}_{date}.log`
- Stage 1 segmentation saves masks to `data/processed/masks/` for visual inspection
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
