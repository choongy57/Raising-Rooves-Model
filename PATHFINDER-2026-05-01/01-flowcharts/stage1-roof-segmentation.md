# Stage 1: Roof Segmentation — Flowchart
**Entry:** `stage1_segmentation/run_stage1.py:24`

## Happy Path

```mermaid
flowchart TD
    A["main<br/>run_stage1.py:24"] --> B["setup_logging<br/>run_stage1.py:93"]
    B --> C["run_stage1<br/>pipeline.py:365"]
    C --> D["get_suburb<br/>pipeline.py:397"]
    D --> E["download_tiles<br/>tile_downloader.py:91"]
    C --> F["_tile_extended_bbox<br/>pipeline.py:45"]
    C --> G["_query_pipeline_footprints<br/>pipeline.py:304"]
    G --> H["query_buildings_in_bbox<br/>building_footprint_segmenter.py:832"]
    H --> I["_overpass_query HTTP POST<br/>building_footprint_segmenter.py:212"]
    I --> J["_osm_response_to_footprints<br/>building_footprint_segmenter.py:297"]
    J --> K["BuildingFootprint list"]
    K --> L["_classify_buildings_from_tiles<br/>pipeline.py:86"]
    L --> M["cv2.imread tile PNG<br/>pipeline.py:141"]
    M --> N["polygon → pixel projection<br/>building_footprint_segmenter.py:135"]
    N --> O["classify_roof<br/>roof_classifier.py:170"]
    O --> P["_classify_by_hsv → label<br/>roof_classifier.py:128"]
    O --> Q["_hsv_to_absorptance → float<br/>roof_classifier.py:83"]
    Q --> R["absorptance_estimate on BuildingFootprint"]
    P --> R
    R --> S["_building_to_row<br/>pipeline.py:272"]
    S --> T["DataFrame → save_parquet<br/>pipeline.py:484"]
    T --> U["to_csv<br/>pipeline.py:485"]
    T --> V["polygons sidecar JSON<br/>pipeline.py:491"]
    T --> W["save_visualisation<br/>stage1_visualiser.py:235"]
```

## Outputs
- `data/output/stage1_{suburb_key}.parquet`
- `data/output/stage1_{suburb_key}.csv`
- `data/output/stage1_{suburb_key}_polygons.json`
- `data/output/stage1_{suburb_key}_annotated.png`

## External deps
- Google Maps Static API (GOOGLE_MAPS_API_KEY)
- OSM Overpass API (no key)

## Key weak points
1. Dual tile centre calculations (visualiser + geo_utils can drift)
2. lon/lat vs lat/lon coordinate order flipped manually at multiple call sites
3. Confidence score semantics: 1.0 = OSM tag, 0.0–0.7 = HSV, 0.0 = unclassified — three meanings
4. absorptance_uncertainty collected but not propagated to Stage 2 calculation
