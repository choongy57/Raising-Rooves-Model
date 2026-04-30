# NASA POWER vs BARRA2: Irradiance Data Source Comparison for Raising Rooves

**Date:** 2026-05-01  
**Author:** Ryan Choong (Raising Rooves FYP)  
**Purpose:** Justify the irradiance data source choice in Stage 2 and Stage 3 of the pipeline

---

## Summary

BARRA2 is the clearly superior dataset for this project and should be the primary
irradiance and temperature source once NCI access is available. NASA POWER is an
acceptable interim fallback for annual GHI but cannot support the hourly cooling
degree hour calculation in Stage 3. The pipeline currently uses NASA POWER while
BARRA2 access is being arranged (RR-008).

---

## Dataset Specifications

| Property | NASA POWER | BARRA2 (BARRA-C2) | BARRA2 (BARRA-R2) |
|---|---|---|---|
| **Spatial resolution** | ~50 km (~0.5°) | **4.4 km** | 12 km |
| **Temporal resolution** | Monthly climatology; daily from 2000+ | **Hourly** | Hourly |
| **Coverage** | Global | Populated Australia incl. all Victoria | All Australia + NZ |
| **Time period** | 1981–present | 2010–present | 1979–present |
| **Solar variable** | `ALLSKY_SFC_SW_DWN` (kWh/m²/day) | `rsds` — surface downwelling shortwave (W/m²) | `rsds` |
| **Temperature variable** | `T2M` (monthly mean, °C) | `tas` — screen-level temp (K, hourly) | `tas` |
| **Source** | MERRA-2 + CERES/SRB satellite obs | ERA5 boundary + BOM station assimilation | ERA5 boundary + BOM station assimilation |
| **Access** | Free REST API, no account | NCI account + project allocation | NCI account + project allocation |
| **Data format** | JSON via REST | Monthly NetCDF via OPeNDAP | Monthly NetCDF via OPeNDAP |
| **Cost** | Free, unlimited | Free with NCI account (Monash allocation) | Free with NCI account |
| **Typical GHI bias (Australia)** | MBE ≈ 5–15% vs ground stations | Lower — assimilates BOM observations | Lower — assimilates BOM observations |

---

## Why BARRA2 Is Better for This Project

### 1. Spatial resolution matters at suburb scale

NASA POWER's ~50 km grid cell covers all of inner Melbourne as a single value.
Carlton, Richmond, Fitzroy, Dandenong, and Box Hill all receive identical GHI.
BARRA-C2 at 4.4 km can distinguish:

- North-facing vs south-facing urban valleys
- Coastal cooling effects (e.g. Frankston vs Dandenong)
- Urban heat island influence on surface temperature
- Regional variation: Mildura (~2,100 kWh/m²/yr) vs Melbourne (~1,650) vs
  Warrnambool (~1,550) — NASA POWER captures this at the regional scale,
  but BARRA2 resolves it more accurately within Victoria

For the current Carlton test case, NASA POWER returned 1,646 kWh/m²/yr.
BARRA2 at 4.4 km would provide a spatially validated figure for each suburb
separately rather than interpolating from a 50 km centroid.

### 2. Hourly data is essential for Stage 3

The Stage 3 thermal model needs **hourly temperature** to compute cooling degree
hours (CDH) — the standard metric for cooling energy demand in Australian building
energy analysis (NatHERS, NABERS).

NASA POWER only provides **monthly mean temperature** — insufficient for CDH
calculation. Using monthly averages systematically underestimates CDH because
it misses peak afternoon temperatures that drive the majority of cooling demand.

BARRA2 `tas` (hourly, Kelvin) enables:

```
CDH = Σ max(0, T_hourly_celsius − 18.5°C)  for all 8,760 hours/year
```

This is the method used in NatHERS accredited energy rating and is directly
comparable to official Australian building energy benchmarks.

### 3. Australian-specific calibration

BARRA2 uses ERA5 as lateral boundary conditions but assimilates Bureau of
Meteorology surface station observations directly. This means it is
calibrated against actual Australian weather station records — not just
downscaled from a global atmospheric model.

NASA POWER is derived entirely from satellite observations and the MERRA-2
global model with no Australian ground station assimilation.

Published accuracy comparisons of global reanalysis solar products in
Australia (Copper et al. 2018, APVI Solar Research Conference; ARENA
Integrated Solar Radiation report 2016) consistently show that datasets
using local observation assimilation outperform pure satellite-model products
in temperate maritime climates like Melbourne, where cloud cover variability
is high and hard for global models to capture.

---

## Where NASA POWER Is Adequate

NASA POWER is **sufficient** for:

1. **Annual mean GHI at suburb scale** — the ~10% spatial uncertainty is smaller
   than the uncertainty in absorptance estimation (±0.15) and pitch assumptions
   (±7°). For suburb-level cool roof benefit totals, a 5–15% GHI error does
   not materially change policy conclusions.

2. **Rapid prototyping and development** — no account, no VPN, instant REST
   call. Useful during pipeline development before BARRA2 access is established.

3. **Cross-checking BARRA2 outputs** — if BARRA2 returns an implausible GHI
   value, NASA POWER provides a quick sanity check.

NASA POWER is **insufficient** for:

1. **Hourly temperature for CDH** — only monthly means available.
2. **Intra-city spatial variation** — all suburbs within ~50 km get the same value.
3. **Final FYP reporting numbers** — the coarse resolution and lack of local
   calibration means results cannot be cited as "accurate" for individual suburbs.

---

## Recommended Data Source Strategy

| Stage | Source | Rationale |
|---|---|---|
| Stage 2: Annual GHI | BARRA2 `rsds` → NASA POWER fallback | BARRA2 preferred once NCI access available |
| Stage 3: Cooling degree hours | BARRA2 `tas` only | NASA POWER monthly temp too coarse for CDH |
| Stage 3 fallback (no BARRA2) | NatHERS Melbourne climate file | Purpose-built for Australian building energy |
| Cross-validation | NASA POWER | Sanity check on BARRA2 values |

The pipeline already implements this priority chain:
`BARRA2 → user CSV → NASA POWER → Melbourne constant`

For Stage 3 CDH specifically, the pipeline should require BARRA2 or NatHERS
and emit a clear WARNING if falling back to a constant temperature assumption,
since CDH cannot be meaningfully estimated from annual averages.

---

## BARRA2 Access Path (Monash)

NCI (National Computational Infrastructure) accounts are free for Australian
researchers. Monash University has an NCI merit allocation.

Steps:
1. Register at https://my.nci.org.au/mancini/signup
2. Ask supervisor Stuart for the NCI project code
3. Join the project in the NCI self-service portal
4. Test connection: `python -c "from stage2_irradiance.barra_client import test_barra2_connection; print(test_barra2_connection())"`

Data is then accessed via OPeNDAP — no bulk download required.
See RR-008 in the project ticket sheet.

---

## Pipeline Variable Reference

| Variable | BARRA2 name | Units | Conversion for pipeline |
|---|---|---|---|
| Surface solar radiation | `rsds` | W/m² (hourly mean) | `mean(W/m²) × 8760 / 1000` → kWh/m²/yr |
| Screen temperature | `tas` | Kelvin (hourly) | `K − 273.15` → °C; then CDH formula |
| Domain (Melbourne) | `AUS-11` (BARRA-R2) | — | 12 km, full Victoria coverage |
| Domain (Melbourne fine) | `AUS-04` (BARRA-C2) | — | 4.4 km, populated areas only |

Note: Variable names in BARRA2 use CORDEX/CF conventions (`rsds`, `tas`),
not BOM internal names (`av_swsfcdown`, `temp_scrn`). This was a source of
a bug in the original pipeline code, corrected in RR-006 (commit `aeefa98`).

---

## Sources

- [BOM BARRA reanalysis project page](https://www.bom.gov.au/research/projects/reanalysis/)
- [BARRA2 development paper — ResearchGate](https://www.researchgate.net/publication/386087051_BARRA2_Development_of_the_next-generation_Australian_regional_atmospheric_reanalysis)
- [NASA POWER homepage](https://power.larc.nasa.gov/)
- [NASA POWER solar FAQs](https://power.larc.nasa.gov/docs/faqs/solar/)
- [ARENA Integrated Solar Radiation Data Sources report (2016)](https://arena.gov.au/assets/2016/02/Integrated_Solar_Radiation_Data_Sources-Final_Report.pdf)
- [Copper et al. 2018, APVI Solar Research Conference — comparison of solar data sources in Australia](https://apvi.org.au/solar-research-conference/wp-content/uploads/2018/11/09_DI_Copper_J_2018_PAPER.pdf)
- [NCI THREDDS BARRA2 catalog](https://thredds.nci.org.au/thredds/catalog/ob53/output/reanalysis/AUS-11/BOM/ERA5/historical/hres/BARRA-R2/v1/catalog.html)
