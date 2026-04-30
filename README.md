# Raising Rooves Model

Monash University Final Year Project (2026): a data pipeline for modelling
cool roof intervention benefits across Melbourne suburbs.

Team: Ryan, Seamus, Angus, Flynn, Maggie, Gabrielle  
Supervisor: Stuart

## Current Status

- Stage 1 roof segmentation: working.
- Roof pitch extraction from DSM: working as a standalone tool.
- Stage 2 irradiance and cool roof delta: working. NASA POWER provides real GHI
  automatically (no key needed) as the first fallback when BARRA2 is unavailable.
- Stage 3 thermal modelling: working.
- Persistence: no application database. Outputs are CSV, Parquet, JSON, PNG,
  HTML, and cached raw files under `data/`.

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
6. Joins buildings to annual solar irradiance (NASA POWER, user CSV, or BARRA2).
7. Estimates per-building reduction in absorbed solar energy from a cool roof
   treatment.
8. Converts absorbed solar reduction to cooling electricity savings via thermal
   model (Stage 3).
9. Produces interactive map, summary charts, and HTML report (visualise_results).

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
  try BARRA2 → user CSV → NASA POWER → Melbourne default GHI
  calculate energy/co2 reduction
        |
        v
data/output/stage2_{suburb}.parquet
data/output/stage2_{suburb}.csv
        |
        v
Stage 3: thermal model
  absorbed solar delta → heat conducted → cooling load → electricity saved
        |
        v
data/output/stage3_{suburb}.parquet
data/output/stage3_{suburb}.csv
        |
        v
tools.visualise_results
  choropleth map, summary charts, HTML report
        |
        v
data/output/stage2_{suburb}_map.html
data/output/stage2_{suburb}_summary.png
data/output/stage2_{suburb}_report.html
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

### Experimental Gemini + OSM Roof Assessment

This is an opt-in comparison workflow. It does not replace or modify the normal
Stage 1 outputs. It reads existing Stage 1 tables, polygon sidecars, and cached
Google satellite tiles, sends small OSM-outlined building crops to Gemini, and
writes separate comparison files under `data/output/experiments/`.

```bash
# Build crop metadata only; no Gemini API call
python -m tools.run_gemini_osm_experiment --suburb Clayton --max-buildings 5 --dry-run

# Send a small bounded sample to Gemini
python -m tools.run_gemini_osm_experiment --suburb Clayton --max-buildings 5
```

Outputs:

- `data/output/experiments/gemini_osm_stage1_{suburb}.jsonl`
- `data/output/experiments/gemini_osm_stage1_{suburb}.csv`

The Gemini pitch value is a coarse visual estimate only. Use DSM extraction for
measured pitch wherever DSM coverage exists. The experiment defaults to high
Gemini media resolution because small roof details are important for this task.
Its `qa_action` field is the local safety gate: boundary mismatches route to
manual review, non-flat visual pitch routes to DSM, and flat/attribute-only
results may be accepted when confidence and image quality are high.

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

# Validate and import a manually downloaded ELVIS DSM
python -m tools.extract_pitch --suburb Clayton --import-dsm ~/Downloads/clayton_dsm.tif

# Download coarse COP30 fallback (30 m resolution — unreliable for individual buildings)
python -m tools.extract_pitch --suburb Clayton --download-cop30
```

Recommended DSM source:

- ELVIS 1 m LiDAR for most suburbs. ELVIS has no programmatic API — draw your
  bbox at elevation.fsdf.org.au, submit the form, and receive a download link by
  email. Use `--import-dsm` to validate and register the downloaded file.
- City of Melbourne DSM for inner-city coverage.
- OpenTopography COP30 only as a coarse fallback. At 30 m resolution, individual
  building pitches are unreliable; use only for suburb-level sanity checks.

The pitch tool uses RANSAC and SVD plane fitting over DSM points inside each
building polygon. It writes `stage1_{suburb}_with_pitch.parquet/csv` and a pitch
map PNG.

## Running Stage 2

```bash
# Uses BARRA2 if available; otherwise NASA POWER (auto, no key needed);
# otherwise user CSV; otherwise Melbourne default GHI (~1850 kWh/m²/yr)
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

## Running Stage 3

Stage 3 reads Stage 2 output and applies a thermal physics chain to produce
per-building cooling electricity savings.

```bash
python -m stage3_thermal.run_stage3 --suburb "Carlton"
python -m stage3_thermal.run_stage3 --suburb "Carlton" --debug
```

Prerequisites: Stage 2 output must exist (`data/output/stage2_{suburb}.parquet`).

### Stage 3 Outputs

Stage 3 appends these columns to the Stage 2 table:

| Column | Description |
| --- | --- |
| `heat_to_interior_kwh_yr` | Solar heat conducted through roof to building interior |
| `cooling_load_reduction_kwh_yr` | Reduction in cooling load (subset of heat to interior) |
| `electricity_saved_kwh_yr` | Actual cooling electricity saved (after HVAC COP) |
| `co2_electricity_saved_kg_yr` | CO2 avoided from the electricity saving |

Output files:

- `data/output/stage3_{suburb}.parquet`
- `data/output/stage3_{suburb}.csv`

### Stage 3 Thermal Parameters

| Parameter | Value | Description |
| --- | --- | --- |
| Roof thermal resistance | R2.5 | Typical uninsulated Australian tile roof |
| Heat transfer fraction | 0.65 (residential), 0.40 (4+ storeys) | Fraction of absorbed solar conducted to interior |
| Cooling fraction | 0.70 | Fraction of interior heat gain driving active cooling |
| HVAC COP | 3.0 (residential), 4.0 (commercial) | Split system / VRF baseline |

As a result, `electricity_saved_kwh_yr` is approximately 13–22% of
`energy_saved_kwh_yr` from Stage 2.

## Visualisation

Produces an interactive map, summary charts, and HTML report from Stage 2 output.

```bash
python -m tools.visualise_results --suburb "Carlton"
python -m tools.visualise_results --suburb "Carlton" --debug
```

Outputs written to `data/output/`:

| File | Description |
| --- | --- |
| `stage2_{suburb}_map.html` | Interactive choropleth — building polygons coloured by energy saved |
| `stage2_{suburb}_summary.png` | 2×2 chart panel (distribution, by material, counts, summary stats) |
| `stage2_{suburb}_report.html` | HTML report with KPI tiles, embedded chart, and map link |

## Running The Full Pipeline

```bash
python -m stage1_segmentation.run_stage1 --suburb Carlton \
  --merge-footprint-file data/raw/footprints/buildings_index.gpkg
python -m stage2_irradiance.run_stage2 --suburb Carlton
python -m stage3_thermal.run_stage3 --suburb Carlton
python -m tools.visualise_results --suburb Carlton
```

## BARRA2 And Grid Handling

There is no fixed 12 by 12 grid assumption in the code.

Current behaviour:

- Direct BARRA2/ERA5 path samples the nearest climate point to the suburb
  centroid and applies that scalar to all buildings.
- NASA POWER (auto-fetched fallback): samples a grid across the suburb bbox at
  0.1° spacing and caches results under `data/raw/nasa_power/`. At ~50 km
  resolution, most Melbourne suburbs will return one or a few data points.
- CSV irradiance input accepts any number of rows.
- Building centroids are matched to the nearest CSV row using latitude/longitude
  distance.

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

## QA Ticket System

Test failures are automatically triaged and written to a Google Sheet as
structured tickets.

```bash
python -m tools.test_monitor              # run tests, auto-create tickets for failures
python -m tools.test_monitor --dry-run    # parse without writing to sheet
python -m tools.test_monitor --list       # print open tickets
python -m tools.test_monitor --triage-only
```

Requires `GOOGLE_SHEET_ID` and `GWS_CREDS_FILE` in `.env`.

## Latest Clayton Validation Snapshot

Latest local run: 2026-04-29.

Stage 1 was run with OSM as the primary source plus the local footprint
GeoPackage supplement. Outputs:

- `stage1_clayton.csv`: 8,024 buildings
- `stage1_clayton.parquet`
- `stage1_clayton_polygons.json`
- `stage1_clayton_annotated.png`: 12,736 x 12,224 PNG

Stage 1 validation:

- 0 duplicate `building_id`
- 0 duplicate `roof_id`
- 7,579 HSV-classified roofs
- 445 unclassified roofs
- Source mix: 2,827 `osm` rows and 5,197 `msft` supplement rows

Stage 2 was run without a Clayton irradiance CSV. BARRA2/ERA5 was unavailable.
NASA POWER returned a measured GHI for Carlton of approximately 1,646 kWh/m²/yr
(vs the 1,850 kWh/m²/yr Melbourne default — 11% lower). Results are cached
under `data/raw/nasa_power/`.

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
7. Stage 3 thermal parameters (R-value, COP, heat transfer fraction) are
   Melbourne residential defaults. Commercial and high-rise buildings use
   adjusted values but no per-building insulation data is available.
8. `--max-tiles` is not a reliable spatial smoke-test cap in the current Stage 1
   pipeline because later steps still use the full tile folder/query extent.
9. Some footprint sources map large compounds as one building polygon rather
   than individual roof blocks. Those roofs need a better authoritative source
   or an explicit computer-vision/manual correction workflow.

## What Needs To Change For The Final Model

### High Priority

- Add true suburb polygon boundaries and an `inside_suburb`/intersection-area
  rule.
- Draw the suburb boundary on annotations.
- Produce a presentation annotation that highlights in-boundary buildings and
  mutes or hides buffer buildings.
- Use measured DSM pitch for final suburbs.
- Connect real annual GHI from BARRA2 when NCI access is available.

### Medium Priority

- Replace assumed pitch with measured DSM pitch for final suburbs.
- Validate absorptance lookup against local building stock data.
- Add true suburb polygon boundaries (ABS SA2).
- Expand to 3+ suburbs for comparison.
- Improve and validate roof material classification.
- Add output summaries by suburb and roof class.
- Add tests around boundary filtering, unit conversions, and irradiance matching.

## Data Sources

| Data | Source | Status |
| --- | --- | --- |
| Satellite imagery | Google Maps Static API | Active; key required |
| Building footprints | OpenStreetMap Overpass API | Active but can fail/reject large queries |
| Local footprint index | GeoPackage built by `tools.build_footprint_index` | Active when present |
| Footprint supplement | VicMap BUILDING_POLYGON or Overture/Microsoft-style data | Manual download/build |
| Solar irradiance (primary) | BARRA2 via NCI THREDDS/OPeNDAP | Intended primary; NCI access required |
| Solar irradiance (auto) | NASA POWER REST API | No key needed; auto-fetched; cached under `data/raw/nasa_power/` |
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
      nasa_power/
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
    nasa_power_client.py
    temperature_processor.py
  stage3_thermal/
    pipeline.py
    run_stage3.py
    thermal_calculator.py
  tools/
    analyse_coordinate.py
    build_footprint_index.py
    extract_pitch.py
    visualise_results.py
    ticket_manager.py
    triage_agent.py
    test_monitor.py
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
