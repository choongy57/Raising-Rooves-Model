# Raising Rooves Model

Monash University Final Year Project (2026).
Data pipeline to model cool roof treatment benefits across Melbourne suburbs.

**Team:** Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle — **Supervisor:** Stuart

---

## What it does

For any Melbourne suburb, the pipeline:
1. Downloads satellite imagery and queries building footprints from OpenStreetMap
2. Classifies each roof's material and colour from satellite pixels (HSV classifier)
3. Assigns an assumed roof pitch based on building type
4. Computes per-building cool roof benefit — how much energy and CO2 is saved by applying a reflective cool roof coating

**Current output:** A CSV of every building in the suburb with area, material, colour, pitch, energy saved (kWh/yr), and CO2 avoided (kg/yr).

---

## Pipeline overview

```
Stage 1: Roof Segmentation          Stage 2: Cool Roof Delta
─────────────────────────           ─────────────────────────
Suburb name                         Stage 1 CSV
    │                                   │
    ▼                                   ▼
Download satellite tiles            Load irradiance data
    │                               (CSV or Melbourne default)
    ▼                                   │
Query OSM building footprints           ▼
  + merge VicMap if provided        Match each building to
    │                               nearest irradiance grid cell
    ▼                                   │
HSV pixel classifier                    ▼
  → roof_material, roof_colour      Calculate per-building:
    │                                 energy_saved_kwh_yr
    ▼                                 co2_saved_kg_yr
Assumed pitch                           │
  → pitch_deg                           ▼
    │                               stage2_{suburb}.csv
    ▼
stage1_{suburb}.csv
stage1_{suburb}_annotated.png
```

---

## Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
```

**2. Create `.env`**
```bash
cp .env.example .env
```
Add your Google Maps Static API key:
```
GOOGLE_MAPS_API_KEY=your_key_here
```
Get one at: [Google Cloud Console](https://console.cloud.google.com/) → Maps Static API.

**3. Verify**
```bash
python -c "from config.settings import *; print('Config OK')"
```

---

## Running the pipeline

### Stage 1 — Roof segmentation

```bash
# Full run for a suburb (downloads tiles, queries OSM, classifies roofs)
python -m stage1_segmentation.run_stage1 --suburb "Carlton"

# Skip tile download if you've already downloaded them
python -m stage1_segmentation.run_stage1 --suburb "Carlton" --skip-download

# Merge with VicMap building polygons (fills gaps in OSM coverage)
python -m stage1_segmentation.run_stage1 --suburb "Carlton" \
  --merge-footprint-file "data/raw/footprints/.../BUILDING_POLYGON.shp"

# Smoke test (first 10 tiles only)
python -m stage1_segmentation.run_stage1 --suburb "Carlton" --max-tiles 10

# List available suburbs
python -m stage1_segmentation.run_stage1 --list-suburbs
```

**Stage 1 outputs** (in `data/output/`):

| File | Contents |
|------|----------|
| `stage1_{suburb}.csv` | Per-building: area, lat/lon, material, colour, shape, pitch, classifier confidence |
| `stage1_{suburb}.parquet` | Same data, Parquet format for Stage 2 |
| `stage1_{suburb}_annotated.png` | Satellite image with coloured building polygon overlays |

**Stage 1 output columns:**

| Column | Description |
|--------|-------------|
| `building_id` | OSM way/relation ID |
| `area_m2` | Roof footprint area in m² |
| `lat`, `lon` | Building centroid |
| `source` | `osm` or `vicmap` |
| `building_type` | OSM building tag (e.g. `residential`, `commercial`) |
| `levels` | Number of storeys from OSM |
| `roof_material` | From OSM tag, or HSV pixel classifier |
| `roof_colour` | From OSM tag, or HSV pixel classifier |
| `roof_shape` | From OSM tag (e.g. `flat`, `gabled`, `hipped`) |
| `pitch_deg` | Assumed pitch — see table below |
| `classifier_confidence` | `1.0` = OSM tag used; `<1.0` = pixel classifier; `0.0` = unclassified |

---

### Stage 2 — Cool roof delta

```bash
# Using Melbourne default GHI (~1850 kWh/m²/yr) — works right now, no extra data needed
python -m stage2_irradiance.run_stage2 --suburb "Carlton"

# Using your own irradiance CSV (recommended when BARRA2 data is available)
python -m stage2_irradiance.run_stage2 --suburb "Carlton" \
  --irradiance-file data/raw/barra/carlton_ghi.csv
```

**Irradiance CSV format** (one row per grid cell):
```
lat,lon,annual_ghi_kwh_m2
-37.80,144.96,1852.3
-37.84,144.96,1849.1
```

**Stage 2 outputs** (appended to Stage 1 columns):

| Column | Description |
|--------|-------------|
| `annual_ghi_kwh_m2` | Annual solar irradiance at building location (kWh/m²/yr) |
| `absorptance_before` | Estimated pre-treatment solar absorptance (0–1) |
| `roof_surface_area_m2` | Actual roof surface area = footprint / cos(pitch) |
| `energy_incident_kwh_yr` | Total solar energy on roof footprint per year |
| `energy_saved_kwh_yr` | Reduction in absorbed solar energy after cool roof treatment |
| `co2_saved_kg_yr` | CO2 avoided using Victorian grid emissions factor (0.79 kg/kWh) |

---

## How the physics works

### Absorptance

Each roof is assigned a **solar absorptance** value — the fraction of incident solar radiation that is absorbed (converted to heat) rather than reflected.

| Roof colour / material | Absorptance before treatment |
|------------------------|------------------------------|
| White | 0.25 |
| Light grey | 0.50 |
| Dark grey / dark metal | 0.85 |
| Red / terracotta | 0.75 |
| Light metal (Colorbond Surfmist etc.) | 0.45 |
| Unknown | 0.75 (conservative) |

After a cool roof coating is applied, absorptance drops to **0.20** (the assumed target — typical for white elastomeric coatings meeting SRI ≥ 78).

### Energy calculation

```
roof_surface_area_m2  =  area_m2 / cos(pitch_deg)
energy_incident       =  annual_ghi_kwh_m2 × area_m2        ← footprint, not surface
energy_saved          =  energy_incident × (absorptance_before − 0.20)
co2_saved             =  energy_saved × 0.79 kg/kWh
```

> **Note on `energy_saved`:** This represents the reduction in solar radiation absorbed by the roof surface — not directly the reduction in building electricity consumption. Translating to cooling electricity savings requires thermal modelling of the roof assembly (thermal resistance, indoor/outdoor temperature difference, HVAC efficiency). That is the job of Stage 3.

### Assumed roof pitch

When no DSM is available, pitch is assumed from OSM tags:

| Building type | Assumed pitch |
|---------------|--------------|
| `flat` roof shape (OSM tag) | 0° |
| 4+ storeys | 0° (flat roof) |
| `commercial`, `retail`, `office`, `hospital` | 0° |
| `industrial`, `warehouse`, `factory` | 5° |
| `garage`, `school`, `shed` | 15° |
| `church`, `cathedral`, `temple` | 30° |
| Residential / unknown | **22.5°** (Melbourne suburban default) |

For a sensitivity analysis, re-run Stage 2 with `pitch_deg` overridden to 15° and 30° — the `energy_saved` delta should be small since pitch affects surface area but not incident energy on the footprint.

---

## What needs to change for the final model

These are known simplifications that should be addressed before the model is used for policy conclusions:

### 1. Replace assumed pitch with measured pitch (HIGH PRIORITY)
**Current:** `pitch_deg` is inferred from building type — all houses get 22.5°.
**Fix:** Use a 1m DSM (Digital Surface Model) to measure actual pitch per building.
- Inner Melbourne (Carlton, Richmond, Footscray): City of Melbourne DSM at data.melbourne.vic.gov.au
- Outer suburbs: requires ELVIS or state LiDAR — check availability per suburb
- Tool is already built: `python -m tools.extract_pitch --suburb Carlton --dsm-file <path>`

### 2. Connect real irradiance data from BARRA2 (HIGH PRIORITY)
**Current:** Melbourne-wide default GHI (~1850 kWh/m²/yr) applied to all buildings uniformly.
**Fix:** Query BARRA2 (BOM reanalysis, 4km grid) for per-suburb annual GHI.
- BARRA2 client is already built in `stage2_irradiance/barra_client.py` — needs NCI THREDDS access
- Alternatively, export annual GHI from BARRA2 manually and provide as a CSV via `--irradiance-file`
- Variable: `av_swsfcdown` (W/m²) — convert: mean W/m² × 8760 / 1000 = annual kWh/m²/yr

### 3. Add thermal modelling — Stage 3 (NEXT STAGE)
**Current:** `energy_saved_kwh_yr` = reduction in absorbed solar radiation on the roof surface.
**What it should be:** Reduction in building cooling energy demand (kWh electricity).
**Fix:** Apply a roof thermal model:
  - Solar Heat Gain through roof → depends on roof R-value (insulation), indoor/outdoor ΔT
  - Cooling energy saved ≈ heat_gain_reduction / COP_of_aircon
  - Typical COP for split system: 3.0–4.5
  - Typical fraction of absorbed solar that becomes indoor heat load: 10–30%

### 4. Improve roof material classification (MEDIUM PRIORITY)
**Current:** HSV (colour-based) pixel classifier — rule-based heuristic, ~50–70% accuracy.
**Fix options (in order of effort):**
  - Fine-tune a small CNN on labelled Melbourne aerial imagery
  - Use Gemini Vision API for direct material classification from roof image patches (free tier: 1500 req/day)
  - Use Overture Maps or other datasets if OSM tag coverage improves

### 5. Expand suburb coverage (LOW PRIORITY)
**Current:** 7 suburbs defined in `config/suburbs.py`.
**Fix:** Add more suburbs — each needs `(south, west, north, east)` bbox from bboxfinder.com.

---

## Data sources

| Data | Source | Status |
|------|--------|--------|
| Satellite imagery | Google Maps Static API (key required) | Active |
| Building footprints | OpenStreetMap via Overpass API | Active |
| Building footprints (supplement) | [VicMap BUILDING_POLYGON](https://datashare.maps.vic.gov.au) — SHP, free | Download manually |
| Solar irradiance | [BARRA2](https://thredds.nci.org.au/thredds/catalog/ob53/catalog.html) via OPeNDAP | NCI access required |
| DSM for pitch (inner Melbourne) | [City of Melbourne Open Data](https://data.melbourne.vic.gov.au) — 1m GeoTIFF | Download manually |
| DSM for pitch (outer suburbs) | [ELVIS](https://elevation.fsdf.org.au) — 1m LiDAR tiles | Download manually |

---

## Project structure

```
Raising Rooves Model/
│
├── stage1_segmentation/
│   ├── pipeline.py                     # Orchestrator: tiles → OSM → classify → save
│   ├── run_stage1.py                   # CLI: --suburb, --merge-footprint-file, etc.
│   ├── building_footprint_segmenter.py # OSM Overpass + VicMap SHP loader + merge
│   ├── roof_classifier.py              # HSV pixel classifier (material + colour)
│   ├── stage1_visualiser.py            # Annotated PNG generator
│   ├── tile_downloader.py              # Google Maps tile fetcher
│   └── _legacy/                        # Old approaches kept for reference
│
├── stage2_irradiance/
│   ├── pipeline.py                     # Orchestrator: Stage 1 + GHI → delta → save
│   ├── run_stage2.py                   # CLI: --suburb, --irradiance-file
│   ├── cool_roof_calculator.py         # Physics: absorptance → energy_saved, co2_saved
│   ├── irradiance_loader.py            # CSV loader + Melbourne default GHI fallback
│   ├── barra_client.py                 # BARRA2 OPeNDAP connector (needs NCI access)
│   ├── era5_fallback.py                # ERA5 fallback (needs CDS_API_KEY in .env)
│   ├── irradiance_processor.py         # BARRA2/ERA5 → monthly GHI stats
│   └── temperature_processor.py        # BARRA2/ERA5 → monthly temperature stats
│
├── tools/
│   ├── analyse_coordinate.py           # MVP: analyse any lat/lon or suburb name
│   ├── extract_pitch.py                # Roof pitch from DSM GeoTIFF (RANSAC)
│   └── build_footprint_index.py        # Build spatial index for large footprint files
│
├── config/
│   ├── settings.py                     # All paths, API endpoints, physics constants
│   └── suburbs.py                      # Melbourne suburb bounding boxes (add more here)
│
├── shared/
│   ├── geo_utils.py                    # Tile maths, coordinate transforms
│   ├── file_io.py                      # Parquet/CSV helpers
│   ├── logging_config.py               # Structured logging (file + console)
│   └── validation.py                   # Env var and data validation
│
├── data/
│   ├── raw/tiles/                      # Downloaded satellite tiles (gitignored)
│   ├── raw/barra/                      # Irradiance NetCDF/CSV cache (gitignored)
│   └── output/                         # stage1_*.csv, stage2_*.csv, annotated PNGs
│
├── research/findings/                  # Research notes (markdown)
├── tests/                              # pytest unit tests
└── requirements.txt
```

### Adding a new suburb

Add an entry to [config/suburbs.py](config/suburbs.py):
```python
"my_suburb": Suburb(
    name="My Suburb",
    sa2_code="",
    centroid=(-37.850, 145.010),
    bbox=(-37.860, 144.995, -37.840, 145.025),  # (south, west, north, east)
    zone_type="residential",
),
```
Get the bbox from [bboxfinder.com](http://bboxfinder.com).

---

## Running tests

```bash
python -m pytest tests/
```
