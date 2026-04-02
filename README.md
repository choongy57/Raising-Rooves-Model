# Raising Rooves Model

Monash University Final Year Project (2026).
Data pipeline to model cool roof treatment benefits across Melbourne suburbs.

**Team:** Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle — **Supervisor:** Stuart

---

## What it does

Given a Melbourne suburb or coordinate, the tool:
1. Downloads satellite imagery from Google Maps
2. Queries building footprint polygons from OpenStreetMap
3. Computes each building's roof area in m²
4. Outputs an annotated satellite image, a CSV of all buildings, and a summary

---

## Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Create your `.env` file**
```bash
cp .env.example .env
```
Open `.env` and add your Google Maps Static API key:
```
GOOGLE_MAPS_API_KEY=your_key_here
```
Get a key: [Google Cloud Console](https://console.cloud.google.com/) → Maps Static API.

**3. Verify**
```bash
python -c "from config.settings import *; print('Config OK')"
```

---

## Usage

### Analyse a coordinate or suburb

```bash
# Single location (~150x150m)
python -m tools.analyse_coordinate --lat -37.9261 --lon 145.1185

# By suburb name
python -m tools.analyse_coordinate --suburb Clayton

# Larger area by radius (recommended — auto-selects grid size)
python -m tools.analyse_coordinate --suburb Clayton --radius 500

# Explicit NxN grid (odd numbers only: 3, 5, 7...)
python -m tools.analyse_coordinate --suburb Clayton --grid 5

# Debug logging
python -m tools.analyse_coordinate --suburb Clayton --debug
```

### Outputs (saved to `data/output/`)

| File | Contents |
|------|----------|
| `<tag>_annotated.png` | Satellite image with coloured building polygon overlays |
| `<tag>_buildings.csv` | Per-building area (m²), centroid lat/lon, OSM building ID |
| `<tag>_summary.txt` | Total buildings, total roof area, coverage % |

---

## How it works

1. **Tile download** — Google Maps Static API fetches 512×512px satellite tiles at zoom 19 (~0.3 m/pixel). Tiles are spaced to stitch seamlessly with no overlap or gap.
2. **OSM query** — One request to the [Overpass API](https://overpass-api.de/) fetches all `building=*` polygons in the bounding box. No API key required.
3. **Area calculation** — Polygon area computed in m² via Shapely (Shoelace formula with lat/lon scaling).
4. **Annotation** — Polygons projected to pixel space and drawn as coloured overlays on the stitched image.

---

## Data sources

| Data | Source |
|------|--------|
| Satellite imagery | Google Maps Static API (key required) |
| Building footprints | OpenStreetMap via Overpass API (no key, always current) |
| Building footprints (alt) | [Microsoft Australia Building Footprints](https://github.com/microsoft/AustraliaBuildingFootprints) — 845MB download, better outer-suburb coverage, pass with `--footprint-file` |

---

## Coming next

- **Stage 2 — Irradiance & Climate Data:** Pull solar irradiance and temperature data from BARRA2 (BOM, 4km resolution) for each suburb to quantify cool roof energy benefits.
- **Stage 3 — Heat Transfer Modelling:** Estimate energy savings and urban heat reduction from cool roof interventions using Stage 1 + Stage 2 outputs.

---

## Running tests

```bash
python -m pytest tests/
```
