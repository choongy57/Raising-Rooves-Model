# Data Pipeline — Literature Review Notes

**Date:** 2026-05-13
**Project:** Raising Rooves — Monash University FYP 2026
**Purpose:** Structured reference for the literature review section: what data the pipeline uses, how, and the methodological basis for each decision.

---

## 1. Overview

The Raising Rooves pipeline estimates the per-building cooling energy benefit of applying a cool roof coating across Melbourne suburbs. It runs in three sequential stages:

| Stage | Function | Output |
|---|---|---|
| Stage 1 | Roof segmentation — extract building footprints, classify roof colour/material, assign pitch | `stage1_{suburb}.parquet/csv` |
| Stage 2 | Irradiance join — attach annual GHI per building, compute absorbed solar delta from cool roof treatment | `stage2_{suburb}.parquet/csv` |
| Stage 3 | Thermal model — convert absorbed solar delta to cooling electricity savings | `stage3_{suburb}.parquet/csv` |

Each stage is independently runnable; outputs chain as inputs to the next stage.

---

## 2. Data Sources

### 2.1 Satellite Imagery — Google Maps Static API

**What:** RGB satellite tiles at zoom level 19 (~0.29 m/pixel at Melbourne latitude), 640×640 px each.

**How it's used:**
- Stage 1 downloads a tile grid covering the suburb bounding box.
- Pixels within each OSM building footprint are extracted and passed to the HSV roof classifier.
- Tiles are cached under `data/raw/tiles/` and reused on re-runs.

**Methodological note:** Google Maps Static API is used rather than open imagery (e.g. Nearmap, Data.vic aerial) for two reasons: (1) free access within rate limits, and (2) globally consistent imagery style simplifies the pixel classifier. The 0.29 m/pixel resolution is sufficient to distinguish roof colour but not fine material texture — this sets a known ceiling on classifier accuracy.

**Limitation for the lit review:** No academic benchmark exists specifically for Google Maps Static API tiles in Australian roof classification. The closest proxy datasets (AIRS, WHU Christchurch, SpaceNet) use dedicated aerial or WorldView imagery. Cross-dataset transfer is an acknowledged source of uncertainty.

---

### 2.2 Building Footprints — OpenStreetMap Overpass API

**What:** Building polygon geometries with optional tag attributes (`building:type`, `roof:colour`, `roof:material`, `building:levels`).

**How it's used:**
- Primary footprint source for Stage 1.
- Each footprint defines the spatial mask for pixel extraction and stores the building metadata used downstream.
- `roof:colour` and `roof:material` OSM tags, where present, bypass the HSV classifier.

**Methodological note:** OSM building coverage in Melbourne is high but tag completeness is low. Published OSM quality assessments for Australian cities (Fan et al. 2014; Barron et al. 2014) report footprint completeness >80% in inner suburbs, declining in outer areas. Roof material tags (`roof:material`) are present on roughly 5–15% of Melbourne buildings — the remainder are classified by the HSV pixel model.

---

### 2.3 Building Footprints (Supplement) — VicMap BUILDING_POLYGON

**What:** Official Victorian government building polygon layer, available as a shapefile from DataShare.

**How it's used:**
- Optional merge with OSM footprints in Stage 1 via `tools/build_footprint_index.py`.
- Fills gaps where OSM footprints are missing or geometry is coarser.
- Stored as a spatially indexed GeoPackage (`data/raw/footprints/buildings_index.gpkg`) for fast lookup.

**Methodological note:** VicMap building polygons are authoritative for planning purposes but may be less current than OSM in rapidly developing areas. Used as a quality supplement, not a replacement.

---

### 2.4 Solar Irradiance — Priority Chain

The pipeline uses a four-level fallback for annual GHI:

```
BARRA2 (NCI OPeNDAP) → user-supplied CSV → NASA POWER REST API → Melbourne constant (1850 kWh/m²/yr)
```

#### 2.4a BARRA2 (Bureau of Meteorology Atmospheric high-resolution Regional Reanalysis for Australia, version 2)

**What:** Australian regional atmospheric reanalysis. Two sub-products used:
- **BARRA-C2 (AUS-04):** 4.4 km grid, hourly, populated Australia only.
- **BARRA-R2 (AUS-11):** 12 km grid, hourly, all Australia.

**Variables used:**
- `rsds` — surface downwelling shortwave radiation (W/m², hourly). Converted to kWh/m²/yr via `mean(W/m²) × 8760 / 1000`.
- `tas` — near-surface temperature (Kelvin, hourly). Converted to °C; used for Stage 3 cooling degree hour (CDH) calculation.

**How it's used:**
- Stage 2: annual GHI per suburb, spatially resolved at 4.4–12 km.
- Stage 3: hourly temperature for CDH computation — the only pathway that supports full thermal modelling.

**Access:** NCI account required; Monash has an NCI project allocation (project ob53). Data accessed via OPeNDAP — no bulk download. See `stage2_irradiance/barra_client.py`.

**Methodological justification:** BARRA2 is the preferred source because (1) 4.4 km spatial resolution distinguishes Melbourne suburbs that a 50 km global product cannot; (2) hourly temperature enables proper CDH calculation, which monthly means cannot support; (3) BOM station assimilation provides Australian-calibrated outputs superior to pure satellite-model products. Published bias for Australian GHI is lower than NASA POWER (Copper et al. 2018).

**Status:** Pipeline supports BARRA2 but NCI access is pending (ticket RR-008). Currently running on NASA POWER fallback.

---

#### 2.4b NASA POWER (Prediction Of Worldwide Energy Resources)

**What:** Global climate data product derived from MERRA-2 reanalysis and CERES/SRB satellite observations. ~50 km spatial resolution.

**Variable used:**
- `ALLSKY_SFC_SW_DWN` — all-sky surface shortwave downward irradiance (kWh/m²/day). Summed to annual kWh/m²/yr.

**How it's used:**
- Stage 2 fallback when BARRA2 is unavailable.
- Returns a single GHI value for the suburb centroid — all buildings in a suburb receive the same irradiance value.
- Free REST API, no account, instantaneous response. Cached under `data/raw/nasa_power/`.

**Methodological justification (and limitation):** NASA POWER is adequate for suburb-level annual totals because GHI spatial variation within a ~5 km suburb is smaller than absorptance uncertainty (±0.15) and pitch uncertainty (±7°). However, it is insufficient for: (1) intra-city spatial variation across Melbourne; (2) Stage 3 CDH calculation (monthly means only); (3) final FYP reporting numbers that need to be cited as accurate for individual suburbs.

**Bias:** MBE ≈ 5–15% vs Australian ground stations (ARENA 2016; Copper et al. 2018). Acceptable for development; not acceptable for cited results.

---

#### 2.4c ERA5 (ECMWF Reanalysis v5)

**What:** Global reanalysis, ~31 km spatial resolution. Hourly data.

**Variable used:**
- `ssrd` — surface solar radiation downwards (J/m²). Converted to kWh.

**How it's used:**
- Code-level fallback in `stage2_irradiance/era5_fallback.py`. CDS API key required.
- Not currently active in the pipeline — NASA POWER is preferred as the interim fallback due to simpler access.

**Methodological note:** ERA5 is widely used in global energy studies (Pfenninger & Staffell 2016; Gruber et al. 2019) but lacks Australian-specific calibration. BARRA2 is derived from ERA5 boundary conditions but adds BOM assimilation — so BARRA2 strictly dominates ERA5 for this application.

---

### 2.5 Digital Surface Model (DSM) — Roof Pitch Extraction

**What:** 1 m resolution LiDAR-derived DSM GeoTIFF, providing above-ground surface height including building roofs.

**Sources (in priority order):**
1. **ELVIS 1 m LiDAR** — Geoscience Australia elevation portal; free registration; best coverage for Melbourne suburbs.
2. **City of Melbourne Open Data DSM** — 1 m, inner-city suburbs only.
3. **OpenTopography COP30** — programmatic API fallback, coarser (30 m) global Copernicus DEM.

**How it's used:**
- `tools/extract_pitch.py` clips the DSM to each building's OSM polygon.
- Point cloud (DSM pixels) is filtered with a MAD-based Z-spike filter, then fit with RANSAC plane detection followed by SVD refit on inliers.
- Output is `pitch_deg` per building, replacing the assumed pitch in the Stage 1 parquet.

**Methodology:**
```
pitch_deg = arctan(slope)  where slope = |normal_z| derived from RANSAC/SVD plane fit
```
Flags: `ok`, `flat`, `unrealistic`, `too_few_points`, `ransac_failed`, `extraction_failed`.

**Methodological note:** RANSAC plane fitting is standard for noisy LiDAR data (Schnabel et al. 2007). The MAD-based outlier filter is more robust than variance-based filters for building point clouds with mixed returns (vegetation, eaves, skylights). The Vicmap Elevation LiDAR Points Collection is the authoritative Victorian source for this application (preferred over the Vicmap 1m DEM which is ground-surface only).

---

### 2.6 Suburb Boundaries — ABS SA2

**What:** Australian Bureau of Statistics Statistical Area Level 2 (SA2) boundaries. Official Australian geographic unit for neighbourhood-scale analysis.

**How it's used:**
- Suburb bounding boxes for tile grid computation and OSM queries are defined in `config/suburbs.py`.
- SA2 codes provide a consistent identifier for cross-suburb comparison.

**Methodological note:** SA2 boundaries are preferred over informal suburb boundaries because they are: (1) consistent with ABS population and building stock statistics used for extrapolation; (2) non-overlapping, preventing double-counting in city-wide rollups.

---

## 3. Derived / Computed Data

### 3.1 Roof Material and Colour Classification (HSV Classifier)

**Input:** Google Maps satellite tile pixels clipped to each OSM building footprint.

**Method:** Hue-Saturation-Value (HSV) colour space clustering. Dominant colour cluster assigned from a predefined palette; saturation and value thresholds distinguish metal (low saturation, high value) from terracotta (high hue, moderate saturation).

**Output columns:** `roof_colour`, `roof_material`, `classifier_confidence`.

**Absorptance assignment:**

| Colour | Solar absorptance |
|---|---|
| White | 0.25 |
| Light grey | 0.50 |
| Dark grey | 0.85 |
| Red / brown / blue / green | 0.75 |
| Unknown | 0.75 (conservative) |

Sources: CSIRO cool roof research; NatHERS material library; AS/NZS 4859.1.

**Methodological note:** HSV classification is a well-established low-compute approach for roof colour extraction from multispectral imagery (Mavromatidis et al. 2006; Ordóñez & Ruano 2013). Its primary limitation is sensitivity to shadowing and image compression artefacts in JPEG satellite tiles. The ±0.15 absorptance uncertainty from this method is larger than the GHI uncertainty from NASA POWER (~0.10), making absorptance the dominant uncertainty source in Stage 2.

**Material priors used for validation:**
- Metal: ~47.5%; Concrete tile: ~17.5%; Terracotta: ~15%; Other: ~20% (CSR VIC regional data).

---

### 3.2 Cool Roof Energy Benefit (Stage 2 Physics)

**Core equation:**
```
energy_incident_kwh_yr  = annual_ghi_kwh_m2 × area_m2
roof_surface_area_m2    = area_m2 / cos(pitch_rad)
energy_saved_kwh_yr     = energy_incident_kwh_yr × (absorptance_before − 0.20)
co2_saved_kg_yr         = energy_saved_kwh_yr × 0.79  [kg CO2-e/kWh, AEMO 2023]
```

**Assumptions and sources:**
- Cool roof post-treatment absorptance = 0.20 (target SRI ≥ 78; consistent with CSIRO Cool Roof Guidelines 2012; aligned with NCC Section J cool roof requirements).
- Energy intercepted by a tilted surface equals GHI × horizontal footprint area — pitch does not change intercepted energy, only distributes it across actual surface area. This is the standard flat-plate collector geometry (Duffie & Beckman, *Solar Engineering of Thermal Processes*, 4th ed.).
- AEMO 2023 Victorian grid emissions factor: 0.79 kg CO2-e/kWh.

**Important scope note for literature review:** `energy_saved_kwh_yr` in Stage 2 represents *reduced absorbed solar energy at the roof surface*, not building electricity savings. Stage 3 applies the thermal transfer chain to convert this to electricity savings. This distinction is critical when citing results — conflating absorbed solar delta with electricity savings overstates the benefit by a factor of ~30–50×.

---

### 3.3 Thermal Model — Electricity Savings (Stage 3 Physics)

**Physics chain:**
```
heat_to_interior_kwh_yr       = energy_saved_kwh_yr × HEAT_TRANSFER_FRACTION
cooling_load_reduction_kwh_yr = heat_to_interior_kwh_yr × COOLING_FRACTION
electricity_saved_kwh_yr      = cooling_load_reduction_kwh_yr / HVAC_COP
co2_electricity_saved_kg_yr   = electricity_saved_kwh_yr × 0.79
```

**Parameter values and justifications:**

| Parameter | Value | Source / Rationale |
|---|---|---|
| `HEAT_TRANSFER_FRACTION` (1–3 storey) | 0.016 | Derived: U_roof (0.40 W/m²K, R2.5 insulated ceiling) / (U_roof + h_outside 25 W/m²K) ≈ 0.016. Consistent with CSIRO "Cool Roofs for Australian Homes" (2012) typical savings. |
| `HEAT_TRANSFER_FRACTION` (4+ storey) | 0.008 | Halved for multi-storey: greater thermal mass and floor slabs attenuate heat path to occupied spaces. |
| `COOLING_FRACTION` | 0.70 | Fraction of interior roof heat gain driving active cooling. Based on NatHERS 6-star house modelling for Melbourne climate. Remainder offset by natural ventilation, thermal mass, night purging. |
| `HVAC_COP` (residential) | 3.0 | GEMS Determination 2019 minimum for 3.5 kW split-system in Melbourne summer conditions. |
| `HVAC_COP` (commercial) | 4.0 | AIRAH DA19 commercial baseline for Melbourne office stock (VRF/central chiller). |

**Cooling Degree Hours (CDH) — intended method:**
```
CDH = Σ max(0, T_hourly_celsius − 18.5°C)  for all 8760 hours/year
```
This is the NatHERS and NABERS standard metric for cooling energy demand in Australian buildings. Currently only available with BARRA2 hourly `tas`. With NASA POWER (monthly means), CDH cannot be computed and the pipeline uses the fixed parameter chain above instead.

**Methodological note:** The three-step thermal chain (absorbed → interior → cooling load → electricity) follows the approach used in Santamouris et al. (2011), Synnefa et al. (2006), and the CSIRO cool roof modelling framework. The HEAT_TRANSFER_FRACTION of 0.016 is the critical parameter — the previously used value of 0.65 (no insulation assumption) was corrected after review; the current 0.016 is the physically defensible value for an insulated Australian residential roof but should be validated against NatHERS simulation outputs from the supervisor.

---

## 4. Methodology Summary

| Pipeline step | Method | Key data | Primary uncertainty |
|---|---|---|---|
| Footprint extraction | OSM Overpass API query + VicMap merge | OSM building polygons | OSM completeness (~80–90% inner Melbourne) |
| Roof segmentation | HSV pixel clustering on Google Maps tiles | Satellite RGB tiles | Shadow, JPEG compression (absorptance ±0.15) |
| Pitch extraction | RANSAC + SVD plane fit on LiDAR DSM | ELVIS / City of Melbourne 1m DSM | DSM coverage gaps; RANSAC convergence on complex roofs |
| Absorptance estimation | Colour/material lookup tables (CSIRO/NatHERS sources) | HSV classification or OSM tags | ±0.15 (dominant uncertainty) |
| Annual GHI | BARRA2 (4.4 km, primary) / NASA POWER (50 km, interim) | `rsds` (W/m², hourly) → kWh/m²/yr | NASA POWER: ±5–15% vs ground stations |
| Cool roof delta | GHI × footprint_area × Δabsorptance | Stage 1 + Stage 2 outputs | Absorptance uncertainty dominates |
| Thermal transfer | Fixed-parameter U-value chain | HEAT_TRANSFER_FRACTION = 0.016 | Needs NatHERS validation; single biggest model assumption |
| Cooling load | Fixed COOLING_FRACTION = 0.70 | NatHERS 6-star Melbourne house | Climate-zone variability not yet captured |
| Electricity savings | cooling_load / HVAC_COP | GEMS 2019 COP values | COP varies by age and maintenance of stock |

---

## 5. Key Literature to Cite

| Citation | Relevance |
|---|---|
| Santamouris et al. (2011), *Energy and Buildings* | Cool roof energy savings quantification — urban heat island co-benefits |
| Synnefa et al. (2006), *Solar Energy* | Absorptance-to-savings thermal model; basis for energy chain approach |
| CSIRO "Cool Roofs for Australian Homes" (2012) | Australian context; absorptance values; typical savings estimates |
| Duffie & Beckman, *Solar Engineering of Thermal Processes* (4th ed.) | Flat-plate collector geometry — GHI × footprint = energy intercepted |
| AEMO 2023 Victorian grid emissions factor (0.79 kg CO2-e/kWh) | CO2 conversion factor for electricity savings |
| NatHERS (AccuRate / FirstRate) Melbourne climate file | CDH base temperature 18.5°C; COP benchmarks |
| GEMS Determination 2019 | HVAC COP minimum 3.0 for residential split-system |
| AIRAH DA19 | COP 4.0 for commercial HVAC baseline |
| Copper et al. (2018), APVI Solar Research Conference | NASA POWER vs ground station accuracy for Australian GHI |
| BOM BARRA2 development paper (ResearchGate 2024) | BARRA2 dataset specification and validation |
| AS/NZS 4859.1 | Solar absorptance values for roof materials |
| Barron et al. (2014), *IJGIS* | OSM completeness methodology |
| Schnabel et al. (2007), *Computer Graphics Forum* | RANSAC plane detection for point clouds |

---

## 6. Known Gaps and Limitations for the Literature Review

1. **No Melbourne-specific roof material dataset exists** — absorptance assignment relies on classifier confidence and material priors from CSR VIC aggregate statistics. Individual building absorptance error is ±0.15.

2. **BARRA2 NCI access pending** — current results use NASA POWER (~50 km resolution). All suburb-level numbers should be noted as interim until BARRA2 is integrated (ticket RR-008).

3. **HEAT_TRANSFER_FRACTION = 0.016 is unvalidated** — derived from U-value physics but not yet cross-checked against NatHERS simulation outputs. This is the single largest modelling assumption and must be flagged in the methodology section.

4. **Stage 2 energy_saved ≠ electricity savings** — the pipeline is explicit about this, but literature comparisons must use Stage 3 `electricity_saved_kwh_yr`, not Stage 2 `energy_saved_kwh_yr`, when comparing to published building energy savings estimates.

5. **Roof pitch currently assumed for most buildings** — DSM extraction (`tools/extract_pitch.py`) is a standalone tool but has not been run at scale over Carlton and Clayton test suburbs. Assumed pitch introduces ~10–20% uncertainty in roof surface area calculation (which affects costing, not energy savings).

6. **Urban Heat Island (UHI) feedback not modelled** — the pipeline models per-building absorbed solar reduction, not the city-scale albedo feedback loop. A true UHI co-benefit (ambient temperature reduction from widespread cool roof adoption) is acknowledged in the literature (Santamouris 2014) but is out of scope for this pipeline.
