# Changelog

## 2026-05-01

### Stage 3 thermal model (new)

Built `stage3_thermal/` — converts Stage 2 absorbed solar reduction into actual
cooling electricity savings via a thermal chain: heat conducted through roof →
cooling load reduction → electricity saved at HVAC COP 3.0.
Carlton result: ~14% of Stage 2 absorbed solar figure becomes electricity saving.
Run: `python -m stage3_thermal.run_stage3 --suburb <name>`

### NASA POWER real irradiance (new)

Added `stage2_irradiance/nasa_power_client.py`. Stage 2 now fetches real annual GHI
from NASA's free REST API (no key required) instead of using a Melbourne constant.
Carlton measured GHI: 1,646 kWh/m²/yr (vs 1,850 assumed — 11% lower).
Results cached under `data/raw/nasa_power/`.

### Data visualisation (new)

Built `tools/visualise_results.py` — produces interactive Folium choropleth map,
matplotlib summary charts, and an HTML report from Stage 2 output.
Run: `python -m tools.visualise_results --suburb <name>`

### Google Sheets QA ticket system (new)

Built `tools/ticket_manager.py`, `tools/triage_agent.py`, `tools/test_monitor.py`.
Test failures are auto-triaged and written to a Google Sheet as structured tickets.
Run: `python -m tools.test_monitor`

### Stage 1 coverage diagnostics (fix)

Added drop-reason counters to `_osm_response_to_footprints` and
`_classify_buildings_from_tiles`. Fixed degenerate polygon projection bug where
buildings at tile edges had all vertices clamped to one edge, silently producing
zero-area masks. Off-canvas annotation guard added to visualiser.

### Pitch extractor: --import-dsm flag (new)

`tools/extract_pitch.py` now accepts `--import-dsm <path>` to validate and import
a manually-downloaded DSM (CRS check, resolution check, bbox overlap). ELVIS 1m
LiDAR has no public API — manual download from elevation.fsdf.org.au required.
`--download-cop30` now warns that 30m resolution is unreliable for individual buildings.

---

## 2026-04-29

### Stage 1 footprint coverage improvements

Extended Overpass query bbox to match actual tile imagery (~75m buffer).
Added local GeoPackage footprint index auto-detection (buildings_index.gpkg).
Carlton run: 6,160 buildings (6,048 OSM + 112 Microsoft fill-ins).

### Stage 2 cool roof delta (working)

Stage 2 pipeline functional with CSV irradiance fallback and Melbourne default GHI.
Per-building absorptance lookup from roof_colour/roof_material. CO2 factor 0.79 kg/kWh.
