# Stage 2: Irradiance + Cool Roof Delta — Flowchart
**Entry:** `stage2_irradiance/run_stage2.py:32`

## Happy Path

```mermaid
flowchart TD
    A["main<br/>run_stage2.py:32"] --> B["run_stage2<br/>pipeline.py:151"]
    B --> C["read_parquet stage1<br/>pipeline.py:187"]
    C --> D["GHI Fallback Chain<br/>pipeline.py:198-255"]
    D --> D1["1. BARRA2 OPeNDAP<br/>barra_client.py:157"]
    D --> D2["2. User CSV<br/>irradiance_loader.py:42"]
    D --> D3["3. NASA POWER REST<br/>nasa_power_client.py:41"]
    D --> D4["4. Melbourne default 1850<br/>irradiance_loader.py:115"]
    D1 -->|fail| D2
    D2 -->|not provided| D3
    D3 -->|fail| D4
    D1 -->|success| E["Broadcast scalar to buildings<br/>pipeline.py:259"]
    D2 -->|success| F["nearest_ghi per building<br/>irradiance_loader.py:97"]
    D3 -->|success| F
    D4 --> F
    E --> G["calculate_building_benefit<br/>cool_roof_calculator.py:60"]
    F --> G
    G --> H{"absorptance_estimate\npresent?"}
    H -->|yes| I["Use HSV float directly<br/>cool_roof_calculator.py:94"]
    H -->|no| J["_absorptance_from_labels<br/>cool_roof_calculator.py:53"]
    I --> K["energy_incident × (abs_before - 0.20)<br/>cool_roof_calculator.py:98"]
    J --> K
    K --> L["save_parquet stage2<br/>pipeline.py:313"]
    L --> M["to_csv stage2<br/>pipeline.py:314"]
```

## GHI Fallback Chain
BARRA2 → user CSV → NASA POWER → Melbourne constant 1850 kWh/m²/yr

## Key weak points
1. Monthly W/m² → kWh/m²/day uses factor 24 (irradiance_processor.py:57) — should be ~12 effective sun hours
2. Input CSV unit not validated — if user provides kWh/m²/day instead of /yr, silent 365× error
3. absorptance_uncertainty passed to calculator but never used in output
4. irradiance_source tag is suburb-wide, not per-building
5. NASA POWER grid density (0.1° = 11 km) not adaptive to suburb size
