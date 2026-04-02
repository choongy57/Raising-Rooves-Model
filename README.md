# Raising Rooves Model

Monash University Final Year Project (2026).
Data pipeline to model cool roof treatment benefits across Melbourne suburbs.

**Team:** Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle — **Supervisor:** Stuart

---

## What it does

Given a Melbourne suburb (or any lat/lon coordinate), the pipeline:
1. Downloads satellite imagery from Google Maps
2. Queries building footprint polygons from OpenStreetMap
3. Computes each building's roof area in m²
4. Outputs an annotated satellite image, a CSV of all buildings, and a summary

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your `.env` file
```bash
cp .env.example .env
```
Then open `.env` and paste in your Google Maps Static API key:
```
GOOGLE_MAPS_API_KEY=your_key_here
```
Get a key from [Google Cloud Console](https://console.cloud.google.com/) → Maps Static API.

### 3. Verify setup
```bash
python -c "from config.settings import *; print('Config OK')"
```

---

## Usage

### Analyse a single coordinate

```bash
# Single tile (~150x150m)
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185

# By suburb name (uses centroid from config)
python -m tools.analyse_coordinate --suburb Clayton

# Larger area using radius (recommended)
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --radius 500

# Explicit NxN grid (must be odd: 1, 3, 5, 7...)
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185 --grid 5

# Debug logging
python -m tools.analyse_coordinate --suburb Clayton --debug
```

**`--radius` vs `--grid`:** Use `--radius` — you think in metres, the tool picks the right grid size. Use `--grid` only if you need a fixed tile count.

### Outputs (saved to `data/output/`)

| File | Contents |
|------|----------|
| `<tag>_annotated.png` | Satellite image with coloured building polygon overlays |
| `<tag>_buildings.csv` | Per-building: area (m²), centroid lat/lon, OSM building ID |
| `<tag>_summary.txt` | Total buildings, total roof area, coverage % |

### Run Stage 1 for a full suburb

```bash
# Full suburb pipeline (downloads tiles + queries all buildings)
python -m stage1_segmentation.run_stage1 --suburb "Richmond"

# Skip tile download if already done
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --skip-download

# Smoke test (cap at 10 tiles)
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --max-tiles 10

# With local Microsoft Building Footprints file (optional, better coverage)
python -m stage1_segmentation.run_stage1 --suburb "Richmond" --footprint-file data/raw/footprints/australia.geojson
```

Available suburbs: `python -m stage1_segmentation.run_stage1 --list-suburbs`

Stage 1 output: `data/output/stage1_<suburb>.parquet` with columns:
`suburb, building_id, roof_id, area_m2, lat, lon, source`

---

## Data sources

| Data | Source | Notes |
|------|--------|-------|
| Satellite imagery | Google Maps Static API | Requires API key |
| Building footprints | OpenStreetMap via Overpass API | No key needed, continuously updated |
| Building footprints (alt) | Microsoft Australia Building Footprints | 845MB download, better outer-suburb coverage |
| Climate / irradiance | BARRA2 / ERA5 | Stage 2 (not yet implemented) |

---

## Project structure

```
Raising Rooves Model/
├── tools/
│   └── analyse_coordinate.py   # MVP: analyse any lat/lon or suburb
│
├── stage1_segmentation/
│   ├── pipeline.py              # Full suburb pipeline orchestrator
│   ├── run_stage1.py            # CLI entry point
│   ├── building_footprint_segmenter.py  # OSM Overpass API queries
│   └── tile_downloader.py       # Google Maps tile fetcher
│
├── stage2_irradiance/           # Climate data pipeline (in progress)
│
├── config/
│   ├── settings.py              # Paths, API endpoints, constants
│   └── suburbs.py               # Melbourne suburb bounding boxes
│
├── shared/
│   ├── geo_utils.py             # Tile maths, coordinate transforms
│   ├── file_io.py               # Parquet/CSV read-write helpers
│   ├── logging_config.py        # Structured logging setup
│   └── validation.py            # Env var and data validation
│
├── data/
│   ├── raw/tiles/               # Downloaded satellite tiles
│   └── output/                  # Annotated images, CSVs, Parquet files
│
├── research/findings/           # Research notes (markdown)
├── tests/                       # pytest tests
├── CLAUDE.md                    # Instructions for Claude Code
└── requirements.txt
```

---

## How it works (building footprint approach)

1. **Tile download** — Google Maps Static API fetches 512×512px satellite images at zoom 19 (~0.3 m/pixel). For a grid, tiles are spaced 2 web-mercator tile units apart so they stitch seamlessly.

2. **OSM query** — A single POST request to the [Overpass API](https://overpass-api.de/) fetches all buildings tagged `building=*` within the suburb/area bounding box. Returns polygon vertices (lat/lon) and OSM building IDs.

3. **Area calculation** — Each polygon's area is computed in m² using the Shapely library with lat/lon scaling (Shoelace formula).

4. **Annotation** — Polygons are projected from lat/lon to pixel coordinates on the stitched satellite image and drawn with coloured overlays.

---

## Running tests

```bash
python -m pytest tests/
```

---

## Known limitations

- OSM coverage varies — inner Melbourne suburbs are well-mapped; outer suburbs may have gaps. Use `--footprint-file` with the Microsoft dataset for better outer-suburb coverage.
- Google Maps API has usage limits ($200/month free credit covers significant usage at prototype scale).
- Stage 2 (climate/irradiance data) is not yet implemented.
