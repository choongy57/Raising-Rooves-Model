# Roof pitch, roof aspect, and solar-geometry modelling for cool roofs

Date: 2026-04-28

## Research question

How should the Raising Rooves model use roof pitch and roof-facing direction
when estimating solar exposure and cool-roof benefit? Is scaling roof area by
`1 / cos(pitch)` enough, or should Stage 2 adopt the same tilt/azimuth
modelling used in photovoltaic tools?

## Short answer

No. Scaling surface area alone is not enough if the goal is to estimate how
much solar energy actually lands on a tilted roof face.

- `footprint area * GHI` is acceptable for a simple horizontal-energy proxy.
- It is not a roof-orientation model.
- Once pitch matters for solar exposure, roof aspect/azimuth matters too.
- PV tools already solve this with standard solar-geometry and plane-of-array
  (POA) irradiance models.

## What established PV workflows do

### 1. They separate sun position from roof orientation

NREL's Solar Position Algorithm (SPA) computes solar zenith and solar azimuth
from time and location. Those angles are then combined with roof tilt and roof
azimuth to determine angle of incidence on the surface.

Relevant source:
- NREL SPA technical report: https://doi.org/10.2172/15003974

### 2. They model irradiance on the tilted plane, not just on horizontal ground

Sandia PVPMC describes POA irradiance as the sum of:

- beam component
- sky-diffuse component
- ground-reflected component

That is the standard fixed-roof / fixed-panel workflow.

Relevant sources:
- PVPMC POA overview: https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/plane-of-array-poa-irradiance/
- PVPMC POA calculation entry point: https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/plane-of-array-poa-irradiance/calculating-poa-irradiance/

### 3. They treat tilt and azimuth as explicit inputs

PVWatts and PVGIS both require or optimize slope/tilt and azimuth/orientation
for fixed systems.

Relevant sources:
- PVWatts V5 manual: https://pvwatts.nrel.gov/downloads/pvwattsv5.pdf
- PVGIS user manual: https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/pvgis-user-manual_en
- PVGIS grid-connected PV tool: https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/pvgis-tools/grid-connected-pv_en

### 4. They usually need more than annual GHI

PVWatts expects hourly weather data and explicitly uses solar zenith, solar
azimuth, DNI, and DHI in addition to location and system orientation. Sandia's
modelling guidance also notes that if DNI and DHI are not directly available,
they are commonly estimated from GHI using decomposition models.

Relevant sources:
- PVWatts V5 manual: hourly inputs and sun-position workflow
  https://pvwatts.nrel.gov/downloads/pvwattsv5.pdf
- PVPMC DNI page:
  https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/irradiance-insolation/direct-normal-irradiance/
- IEA PVPS / Sandia modelling methods report:
  https://pvpmc.sandia.gov/app/uploads/sites/243/2022/10/Report-IEA-PVPS-T13-06-2017_PV_Performance_Modeling_Methods_and_Practices_SAND2017-2570-R.pdf

### 5. pvlib is the practical open-source implementation path

`pvlib` exposes this workflow directly: compute sun position, then call
`get_total_irradiance()` / `PVSystem.get_irradiance()` using surface tilt,
surface azimuth, DNI, GHI, and DHI.

Relevant source:
- pvlib irradiance API:
  https://pvlib-python.readthedocs.io/en/v0.11.1/reference/generated/pvlib.pvsystem.PVSystem.get_irradiance.html

## Relevance to Raising Rooves

### What the current repo already has

- The DSM pitch tool already estimates both `pitch_deg` and `aspect_deg`.
- `aspect_deg` is defined as the downhill direction the roof face points.

This is enough geometric information for a first fixed-plane irradiance model,
at least for single-plane roofs.

### What the current Stage 2 still assumes

- Stage 2 currently loads `stage1_{suburb}.parquet`, not the pitch-enhanced
  output.
- The current benefit calculation uses:
  - `roof_surface_area_m2 = area_m2 / cos(pitch)`
  - `energy_incident_kwh_yr = annual_ghi_kwh_m2 * area_m2`
- That means pitch is only used for material quantity/costing, not for
  orientation-aware solar incidence.

This is a valid simplification for a proxy model, but it is not equivalent to
PV-style roof-face irradiance modelling.

## Implementation options

### Option A - Keep the current proxy

Use:

`annual_ghi_kwh_m2 * footprint_area_m2`

Good for:
- fast suburb-level comparison
- simple "reduced absorbed solar radiation" proxy
- cases where orientation detail is unavailable

Limitations:
- ignores roof-facing direction
- does not distinguish north-facing vs south-facing pitched roofs
- cannot use the extracted `aspect_deg`

### Option B - Minimal orientation-aware annual model

Use a standard transposition model on a representative time series:

1. For each roof, use `pitch_deg` as tilt and `aspect_deg` as surface azimuth.
2. For each time step, compute solar zenith/azimuth from location and time.
3. Estimate or ingest `GHI`, `DNI`, and `DHI`.
4. Compute POA irradiance on the roof face.
5. Integrate POA over the year.
6. Compute absorbed energy before and after treatment:

`absorbed = annual_poa_kwh_m2 * roof_surface_area_m2 * absorptance`

Good for:
- much better physical meaning
- direct reuse of PV industry methods
- still manageable for Stage 2

Limitations:
- needs hourly or at least sub-daily irradiance
- needs decomposition if only GHI exists
- single-plane assumption is weak for gable and hip roofs

### Option C - Multi-plane roof-face model

Split each building into roof planes/ridges and model each face separately.

Good for:
- the most correct roof geometry
- north/south or east/west split roofs handled properly

Limitations:
- much more geometry work
- DSM plane extraction must become multi-plane, not dominant-plane only
- more expensive to validate

## Recommended path

### Near term

Implement Option B.

Reason:
- It matches standard PV practice closely enough.
- The repo already extracts `aspect_deg`.
- It upgrades Stage 2 without requiring a full roof-plane segmentation project.

### Suggested Stage 2.1 design

Add a new orientation-aware pathway rather than replacing the current proxy
immediately.

Suggested outputs:
- `roof_tilt_deg`
- `roof_azimuth_deg`
- `annual_poa_kwh_m2_yr`
- `energy_incident_poa_kwh_yr`
- `energy_absorbed_before_kwh_yr`
- `energy_absorbed_after_kwh_yr`
- `energy_saved_absorbed_kwh_yr`
- `irradiance_model`
- `orientation_source`

Suggested modelling choices:
- Keep current proxy as a fallback when `aspect_deg` is missing.
- Use hourly BARRA2 if accessible because the repo already targets hourly
  BARRA2 data.
- If only GHI is available, decompose GHI to DNI/DHI using a standard model and
  document the uncertainty.
- Use `pvlib` rather than writing solar geometry and transposition math from
  scratch.

## Important modelling caution

For a cool roof, the target variable is not PV electricity output. It is
absorbed solar energy at the roof surface, and later the fraction of that heat
that enters the conditioned building.

So the clean sequence is:

1. compute POA irradiance on the roof surface
2. multiply by roof absorptance to get absorbed solar energy
3. in Stage 3, translate absorbed heat to indoor cooling load and electricity

That keeps the physics aligned with the project scope.

## Practical conclusion

Pitch alone should not only make the roof area larger.

Use pitch in two different ways:

- for quantities and coating cost: increase surface area
- for solar exposure: combine pitch with roof-facing direction and sun position

If the model stays at annual GHI only, then keeping pitch out of incident-energy
math is defensible.
If the model wants to say "this roof gets more or less sun because of its
orientation", then yes: roof-facing direction is required.

## Sources consulted

1. NREL. Solar Position Algorithm for Solar Radiation Applications (Revised).
   https://doi.org/10.2172/15003974
2. Sandia PVPMC. Plane of Array (POA) Irradiance.
   https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/plane-of-array-poa-irradiance/
3. Sandia PVPMC. Array Orientation.
   https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/array-orientation/
4. Sandia PVPMC. Direct Normal Irradiance.
   https://pvpmc.sandia.gov/modeling-guide/1-weather-design-inputs/irradiance-insolation/direct-normal-irradiance/
5. Dobos, A. PVWatts Version 5 Manual.
   https://pvwatts.nrel.gov/downloads/pvwattsv5.pdf
6. Joint Research Centre, European Commission. PVGIS User Manual.
   https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/getting-started-pvgis/pvgis-user-manual_en
7. Joint Research Centre, European Commission. PVGIS Grid-connected PV tool.
   https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis/pvgis-tools/grid-connected-pv_en
8. pvlib python documentation. `PVSystem.get_irradiance`.
   https://pvlib-python.readthedocs.io/en/v0.11.1/reference/generated/pvlib.pvsystem.PVSystem.get_irradiance.html
9. IEA PVPS / Sandia. PV Performance Modeling Methods and Practices.
   https://pvpmc.sandia.gov/app/uploads/sites/243/2022/10/Report-IEA-PVPS-T13-06-2017_PV_Performance_Modeling_Methods_and_Practices_SAND2017-2570-R.pdf
