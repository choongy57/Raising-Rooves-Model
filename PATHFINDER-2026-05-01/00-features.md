# Feature Inventory — Raising Rooves Model
**Generated:** 2026-05-01

## Pipeline Stages

| # | Feature | Entry Point | Core Files |
|---|---------|-------------|------------|
| 1 | Stage 1: Roof Segmentation | `stage1_segmentation/run_stage1.py:24` | pipeline.py, building_footprint_segmenter.py, roof_classifier.py, tile_downloader.py, stage1_visualiser.py |
| 2 | Stage 2: Irradiance + Cool Roof Delta | `stage2_irradiance/run_stage2.py:32` | pipeline.py, irradiance_loader.py, cool_roof_calculator.py, nasa_power_client.py, barra_client.py, irradiance_processor.py |
| 3 | Stage 3: Thermal Modelling | `stage3_thermal/run_stage3.py:35` | pipeline.py, thermal_calculator.py |

## Tool Features

| # | Feature | Entry Point | Core Files |
|---|---------|-------------|------------|
| 4 | Roof Pitch Extraction | `tools/extract_pitch.py` | stage1_segmentation/pitch_extractor.py |
| 5 | Results Visualisation | `tools/visualise_results.py:14` | folium + matplotlib |
| 6 | Building Footprint Indexing | `tools/build_footprint_index.py:43` | streaming GeoJSONL → GeoPackage |
| 7 | Test Monitor & QA Tickets | `tools/test_monitor.py:28` | ticket_manager.py, triage_agent.py |
| 8 | Gemini OSM Experiment | `tools/run_gemini_osm_experiment.py:21` | gemini_osm_experiment.py |

## Shared Infrastructure

| # | Feature | Files |
|---|---------|-------|
| 9 | Logging | `shared/logging_config.py` |
| 10 | File I/O | `shared/file_io.py` |
| 11 | Geo Utilities | `shared/geo_utils.py` |
| 12 | Suburb Config | `config/suburbs.py` |
| 13 | Global Settings | `config/settings.py` |
