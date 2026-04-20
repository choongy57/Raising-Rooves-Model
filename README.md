# Raising Rooves Model

Monash University Final Year Project (2026): a data pipeline for modelling
cool roof intervention benefits across Melbourne suburbs.

Team: Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle  
Supervisor: Stuart

## Current Status

- Stage 1 roof segmentation: working.
- Roof pitch extraction from DSM: working as a standalone tool.
- Stage 2 irradiance and cool roof delta: working, but real BARRA2 access still
  needs to be finalised.
- Stage 3 thermal modelling: planned.
- Persistence: no application database. Outputs are CSV, Parquet, JSON, PNG,
  and cached raw files under `data/`.

Important note: `data/raw/footprints/buildings_index.gpkg` is a generated
GeoPackage spatial index used for fast local footprint lookup. It is not the
project database and should not be deleted unless you are happy to rebuild it.

## What The Pipeline Does

For a configured Melbourne suburb, the pipeline:

1. Computes a satellite tile grid from the suburb bounding box.
2. Downloads or reuses Google Maps satellite tiles.
3. Queries building footprints from OpenStreetMap and/or local footprint data.
4. Classifies roof colour/material from satellite pixels where tags are missing.
5. Assigns roof pitch from assumptions, or uses the standalone DSM pitch tool.
6. Joins buildings to annual solar irradiance.
7. Estimates per-building reduction in absorbed solar energy from a cool roof
   treatment.

Current Stage 2 output is not final electricity savings. It is reduced absorbed
solar radiation at the roof. Translating that into cooling electricity savings
requires Stage 3 thermal modelling.

## Data Flow

```text
config/suburbs.py
  suburb centroid + bbox
        |
        v
Stage 1: roof segmentation
  compute tile grid
  download/reuse Google satellite tiles
  query OSM footprints and/or local GeoPackage/SHP/GeoJSONL
  classify roof pixels
  assign assumed pitch
        |
        v
data/output/stage1_{suburb}.parquet
data/output/stage1_{suburb}.csv
data/output/stage1_{suburb}_polygons.json
data/output/stage1_{suburb}_annotated.png
        |
        v
Optional pitch improvement
  tools.extract_pitch + DSM GeoTIFF
        |
        v
Stage 2: irradiance + cool roof delta
  try BARRA2/ERA5 climate path
  else load irradiance CSV
  else use Melbourne default GHI
  calculate energy/co2 reduction
        |
        v
data/output/stage2_{suburb}.parquet
data/output/stage2_{suburb}.csv
```

## Data Needed

### Required

- Python dependencies from `requirements.txt`.
- `.env` with `GOOGLE_MAPS_API_KEY` for fresh satellite tile downloads.
- A suburb entry in `config/suburbs.py`.

### Strongly Recommended

- Local footprint index:
  `data/raw/footprints/buildings_index.gpkg`
- Source footprint file for rebuilding the index:
  `data/raw/footprints/melbourne_overture.geojsonl`
- Real irradiance CSV with columns:
  `lat, lon, annual_ghi_kwh_m2`
- DSM GeoTIFF for measured roof pitch, ideally 1 m LiDAR.
- True suburb boundary polygon for final reporting. The current config uses
  rectangular bboxes, not real suburb polygons.

### Optional API Keys

- `CDS_API_KEY` for ERA5 fallback.
- `OPENTOPO_API_KEY` for programmatic COP30 DSM fallback.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Add your Google Maps Static API key to `.env`:

```text
GOOGLE_MAPS_API_KEY=your_key_here
```

Verify the config imports:

```bash
python -c "from config.settings import *; print('Config OK')"
```

## Running Stage 1

```bash
# Full run using OSM plus any auto-detected local supplement index
python -m stage1_segmentation.run_stage1 --suburb "Clayton"

# Debug logging
python -m stage1_segmentation.run_stage1 --suburb "Clayton" --debug

# Reuse existing tiles and skip tile download
python -m stage1_segmentation.run_stage1 --suburb "Clayton" --skip-download

# Use only a local footprint file/index and skip OSM
python -m stage1_segmentation.run_stage1 --suburb "Clayton" \
  --footprint-file data/raw/footprints/buildings_index.gpkg

# Merge a local footprint file with OSM
python -m stage1_segmentation.run_stage1 --suburb "Clayton" \
  --merge-footprint-file data/raw/footprints/buildings_index.gpkg

# List configured suburbs
python -m stage1_segmentation.run_stage1 --list-suburbs
```

Stage 1 auto-detects `data/raw/footprints/buildings_index.gpkg` when it exists
and uses it as a supplement unless `--footprint-file` is passed. In supplement
mode, Stage 1 tries OSM first, then falls back to the local index if Overpass is
blocked or rejects the query. Use `--footprint-file` to skip OSM entirely.

### Stage 1 Outputs

| File | Contents |
| --- | --- |
| `stage1_{suburb}.csv` | Per-building CSV for inspection and reports |
| `stage1_{suburb}.parquet` | Canonical Stage 1 table used by Stage 2 |
| `stage1_{suburb}_polygons.json` | Building polygon sidecar used by pitch extraction |
| `stage1_{suburb}_annotated.png` | Stitched satellite image with building overlays |

### Stage 1 Columns

| Column | Description |
| --- | --- |
| `suburb` | Configured suburb name |
| `building_id` | Source footprint id |
| `roof_id` | Stable project roof id |
| `area_m2` | Building footprint area in square metres |
| `lat`, `lon` | Building centroid |
| `source` | Footprint source, e.g. `osm`, `vicmap`, or `msft` |
| `building_type` | Building tag/type where available |
| `levels` | Number of levels where available |
| `roof_material` | OSM/source tag or HSV classifier estimate |
| `roof_colour` | OSM/source tag or HSV classifier estimate |
| `roof_shape` | Roof shape tag where available |
| `pitch_deg` | Assumed roof pitch unless DSM pitch has been applied |
| `classifier_confidence` | `1.0` for source tags, `0.0` unclassified, otherwise HSV confidence |

## Boundary And Annotation Behaviour

The current suburb definitions use rectangular bboxes. Satellite tiles are fixed
to a web-map grid, so the downloaded imagery always extends beyond the bbox.
Stage 1 then expands the footprint query to match the visible tile area so edge
buildings have overlays.

That means current Stage 1 CSV/parquet outputs can include buildings outside the
configured bbox. For the latest Clayton run, 7,762 buildings were output:

- 6,976 centroid-inside the configured Clayton bbox
- 786 centroid-outside the configured Clayton bbox

For final policy/reporting work, the better design is:

1. Keep the tile buffer for imagery and classification.
2. Use a true suburb polygon, preferably ABS SA2 or another authoritative
   boundary.
3. Add `inside_suburb` and/or intersection-area weighting.
4. Report canonical totals for buildings inside the analysis boundary.
5. Draw the suburb boundary on the annotation.
6. Show buffer buildings muted or omit them from the presentation annotation.

## Roof Pitch Extraction

Use this after Stage 1 when a DSM GeoTIFF is available.

```bash
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif
python -m tools.extract_pitch --suburb Clayton --dsm-file data/raw/dsm/clayton.tif --debug
python -m tools.extract_pitch --suburb Clayton --download-cop30
```

Recommended DSM source:

- ELVIS 1 m LiDAR for most suburbs.
- City of Melbourne DSM for inner-city coverage.
- OpenTopography COP30 only as a coarse fallback.

The pitch tool uses RANSAC and SVD plane fitting over DSM points inside each
building polygon. It writes `stage1_{suburb}_with_pitch.parquet/csv` and a pitch
map PNG.

## Running Stage 2

```bash
# Uses BARRA2/ERA5 if available; otherwise falls back to Melbourne default GHI
python -m stage2_irradiance.run_stage2 --suburb "Clayton"

# Use a prepared irradiance grid CSV
python -m stage2_irradiance.run_stage2 --suburb "Clayton" \
  --irradiance-file data/raw/barra/clayton_ghi.csv

# Debug logging
python -m stage2_irradiance.run_stage2 --suburb "Clayton" --debug
```

Irradiance CSV format:

```csv
lat,lon,annual_ghi_kwh_m2
-37.915,145.122,1850.0
```

### Stage 2 Outputs

Stage 2 appends these columns to the Stage 1 table:

| Column | Description |
| --- | --- |
| `annual_ghi_kwh_m2` | Annual global horizontal irradiance at/near the building |
| `absorptance_before` | Estimated pre-treatment solar absorptance |
| `roof_surface_area_m2` | Roof surface area = footprint area / cos(pitch) |
| `energy_incident_kwh_yr` | Annual incident solar energy on the footprint |
| `energy_saved_kwh_yr` | Reduced absorbed solar energy after cool roof treatment |
| `co2_saved_kg_yr` | CO2 avoided using the configured grid emissions factor |

## BARRA2 And Grid Handling

There is no fixed 12 by 12 grid assumption in the code.

Current behaviour:

- Direct BARRA2/ERA5 path samples the nearest climate point to the suburb
  centroid and applies that scalar to all buildings.
- CSV irradiance input accepts any number of rows.
- Building centroids are matched to the nearest CSV row using latitude/longitude
  distance.
- A 12 by 12 CSV would be accepted as 144 points, but the code does not treat it
  as a structured raster.

BARRA2 is roughly 4 km resolution. Clayton's configured bbox is smaller than a
single 4 km cell in each direction, so raw BARRA2 may only produce one or a few
meaningful grid cells for the suburb.

## Cool Roof Physics

Solar absorptance before treatment is estimated from `roof_colour` first, then
`roof_material`, then a conservative fallback.

| Roof colour/material | Absorptance before treatment |
| --- | --- |
| White | 0.25 |
| Light grey | 0.50 |
| Dark grey / dark metal | 0.85 |
| Red / terracotta | 0.75 |
| Light metal | 0.45 |
| Unknown | 0.75 |

Cool roof treatment target absorptance:

```text
COOL_ROOF_ABSORPTANCE = 0.20
```

Calculation:

```text
roof_surface_area_m2 = area_m2 / cos(pitch_deg)
energy_incident      = annual_ghi_kwh_m2 * area_m2
energy_saved         = energy_incident * (absorptance_before - 0.20)
co2_saved            = energy_saved * 0.79 kg/kWh
```

`energy_incident` uses footprint area, not roof surface area, because GHI is
horizontal irradiance. Roof surface area is still useful for material quantity
and cost estimates.

## Latest Clayton Validation Snapshot

Latest local run: 2026-04-20.

Stage 1 was run with the local footprint GeoPackage because Overpass rejected
the Clayton query. Outputs:

- `stage1_clayton.csv`: 7,762 buildings
- `stage1_clayton.parquet`
- `stage1_clayton_polygons.json`
- `stage1_clayton_annotated.png`: 12,736 x 12,224 PNG

Stage 1 validation:

- 0 duplicate `building_id`
- 0 duplicate `roof_id`
- 7,397 HSV-classified roofs
- 365 unclassified roofs
- Source: `msft` for all rows in this local-only run

Stage 2 was run without a Clayton irradiance CSV. BARRA2/ERA5 was unavailable,
so the Melbourne default GHI was used:

```text
annual_ghi_kwh_m2 = 1850.0
```

Outputs:

- `stage2_clayton.csv`
- `stage2_clayton.parquet`

These numbers are suitable for pipeline validation, not final policy
conclusions.

## Known Limitations

1. Current suburb boundaries are rectangular bboxes, not true suburb polygons.
2. Current canonical outputs can include tile-buffer buildings outside the bbox.
3. OSM Overpass can fail or reject large bbox queries; local footprints are
   needed for reliable reruns.
4. HSV roof classification is heuristic and should be validated.
5. Assumed pitch should be replaced with DSM-derived pitch where possible.
6. BARRA2/ERA5 access is not yet reliable in the current pipeline.
7. Stage 2 reports reduced absorbed solar energy, not electricity savings.
8. `--max-tiles` is not a reliable spatial smoke-test cap in the current Stage 1
   pipeline because later steps still use the full tile folder/query extent.

## What Needs To Change For The Final Model

### High Priority

- Add true suburb polygon boundaries and an `inside_suburb`/intersection-area
  rule.
- Draw the suburb boundary on annotations.
- Produce a presentation annotation that highlights in-boundary buildings and
  mutes or hides buffer buildings.
- Use measured DSM pitch for final suburbs.
- Prepare or connect real irradiance data.

### Medium Priority

- Improve and validate roof material classification.
- Add output summaries by suburb and roof class.
- Add tests around boundary filtering, unit conversions, and irradiance matching.

### Next Stage

Build Stage 3 thermal modelling:

- roof insulation / R-value
- indoor/outdoor temperature difference
- cooling degree days or hourly temperature
- HVAC coefficient of performance
- fraction of absorbed roof heat entering indoor cooling load

## Data Sources

| Data | Source | Status |
| --- | --- | --- |
| Satellite imagery | Google Maps Static API | Active; key required |
| Building footprints | OpenStreetMap Overpass API | Active but can fail/reject large queries |
| Local footprint index | GeoPackage built by `tools.build_footprint_index` | Active when present |
| Footprint supplement | VicMap BUILDING_POLYGON or Overture/Microsoft-style data | Manual download/build |
| Solar irradiance | BARRA2 via NCI THREDDS/OPeNDAP | Intended source; access/path unresolved |
| Irradiance fallback | User CSV or Melbourne default GHI | Active |
| DSM for pitch | ELVIS 1 m LiDAR | Recommended manual download |
| Inner-city DSM | City of Melbourne Open Data | Manual download |
| Coarse DSM fallback | OpenTopography COP30 | Optional; key required |
| Suburb boundaries | ABS SA2 or authoritative polygon data | Needed for final boundary handling |

## Project Structure

```text
Raising Rooves Model/
  config/
    settings.py
    suburbs.py
  data/
    raw/
      tiles/
      barra/
      footprints/
    output/
  research/
    findings/
  shared/
    file_io.py
    geo_utils.py
    logging_config.py
    validation.py
  stage1_segmentation/
    pipeline.py
    run_stage1.py
    building_footprint_segmenter.py
    roof_classifier.py
    stage1_visualiser.py
    tile_downloader.py
    dsm_processor.py
    pitch_extractor.py
    _legacy/
  stage2_irradiance/
    pipeline.py
    run_stage2.py
    barra_client.py
    cool_roof_calculator.py
    era5_fallback.py
    irradiance_loader.py
    irradiance_processor.py
    temperature_processor.py
  tools/
    analyse_coordinate.py
    build_footprint_index.py
    extract_pitch.py
  tests/
  AGENTS.md
  CLAUDE.md
  README.md
  requirements.txt
```

## Adding A New Suburb

Add an entry to `config/suburbs.py`:

```python
"my_suburb": Suburb(
    name="My Suburb",
    sa2_code="",
    centroid=(-37.850, 145.010),
    bbox=(-37.860, 144.995, -37.840, 145.025),
    zone_type="residential",
)
```

For final modelling, also add or reference a true suburb/SA2 boundary polygon
rather than relying only on the bbox.

## Tests

```bash
python -m pytest tests/
```
