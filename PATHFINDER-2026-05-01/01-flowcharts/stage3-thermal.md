# Stage 3: Thermal Modelling — Flowchart
**Entry:** `stage3_thermal/run_stage3.py:35`

## Happy Path

```mermaid
flowchart TD
    A["main<br/>run_stage3.py:35"] --> B["run_stage3<br/>pipeline.py:40"]
    B --> C["read_parquet stage2<br/>pipeline.py:61"]
    C --> D["iterrows<br/>pipeline.py:74"]
    D --> E["calculate_thermal_benefit<br/>thermal_calculator.py:55"]
    E --> F["clamp energy_saved ≥ 0<br/>thermal_calculator.py:93"]
    F --> G["Select HEAT_TRANSFER_FRACTION<br/>thermal_calculator.py:107"]
    G --> G1["levels < 4 → 0.65"]
    G --> G2["levels ≥ 4 → 0.40"]
    G1 --> H["heat_to_interior = energy_saved × H_frac<br/>thermal_calculator.py:112"]
    G2 --> H
    H --> I["cooling_load = heat × 0.70<br/>thermal_calculator.py:113"]
    I --> J["Select HVAC_COP<br/>thermal_calculator.py:99"]
    J --> J1["commercial → 4.0"]
    J --> J2["residential/other → 3.0"]
    J1 --> K["electricity_saved = cooling / COP<br/>thermal_calculator.py:114"]
    J2 --> K
    K --> L["co2_saved = electricity × 0.79<br/>thermal_calculator.py:115"]
    L --> M["save_parquet stage3<br/>pipeline.py:123"]
    M --> N["to_csv stage3<br/>pipeline.py:124"]
```

## Physics Chain
```
heat_to_interior        = energy_saved × 0.65 (or 0.40 for 4+ storeys)
cooling_load_reduction  = heat_to_interior × 0.70
electricity_saved       = cooling_load / COP (3.0 or 4.0)
co2_saved               = electricity_saved × 0.79
```
Net multiplier (typical residential): 0.65 × 0.70 / 3.0 = **0.152**

## Key FYP defensibility risks
1. COOLING_FRACTION = 0.70 — vague NatHERS cite, no specific report
2. HVAC_COP ignores seasonal variation (COP drops to 2.0–2.5 at 35°C — exactly when cool roof matters most)
3. roof_material passed to calculator but never used (dead parameter)
4. No uncertainty output — reports point estimates to 1 decimal place
5. Binary building-type split too coarse — warehouse should have COP→∞ (no active cooling)
