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
# ── MVP: Analyse a single coordinate (Gemini Vision, no GPU needed) ──
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185
python -m tools.analyse_coordinate --suburb Clayton
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --grid 3  # 3×3 tile grid
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --debug

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
Stage 1 uses **Gemini Vision API** (gemini-2.0-flash) for roof segmentation — no GPU or Colab needed.
- Each 640×640 tile is sent to Gemini with a structured JSON prompt
- Gemini returns roof polygon coordinates + material + colour in one call
- Polygons are rendered to binary masks via OpenCV and saved to `data/processed/masks/`
- Run is checkpoint-aware: interrupted runs resume from where they left off
- Rate: free tier ~15 RPM (2208 tiles ≈ 2.5 hrs); paid tier ~2000 RPM (≈ 1.5 min)
- `GEMINI_API_KEY` must be set in `.env` (free key at https://aistudio.google.com/app/apikey)

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
