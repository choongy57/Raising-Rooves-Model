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
# Stage 1: Roof Segmentation
python -m stage1_segmentation.run_stage1 --suburb "Richmond"
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --debug

# Stage 2: Irradiance & Climate Data
python -m stage2_irradiance.run_stage2 --suburb "Richmond"
python -m stage2_irradiance.run_stage2 --suburb "Richmond" --debug

# Tests
python -m pytest tests/
```

## Debugging
- All CLI entry points accept `--debug` to set logging to DEBUG level
- Logs output to both console and `logs/{module}_{date}.log`
- Stage 1 segmentation saves intermediate masks to `data/processed/masks/` for visual inspection

## GPU Note
Local machine has AMD Radeon 760M (no CUDA). SAM3 inference runs on Google Colab.
- Tiles downloaded locally → uploaded to Colab → masks downloaded back
- All other processing runs locally (no GPU needed)

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
