# Stage 1 Methods Note — Roof Attribution Pipeline

*For inclusion in the FYP methodology section. Drafted 2026-07-01.*

---

## Overview

Stage 1 produces one attributed roof record per building in a target Melbourne suburb.
The pipeline is geography-agnostic: any suburb in `config/suburbs.py` can be processed
by changing one argument. Carlton (6177 buildings) and Clayton (8025 buildings) have been
validated end-to-end.

---

## 1. Building Footprint Acquisition

Building footprints are obtained from the OpenStreetMap (OSM) Overpass API
(overpass-api.de). A single bounding-box query is issued per suburb, retrieving all OSM
`building=*` ways and multipolygon relations. Where a locally-indexed GeoPackage of VicMap
or Microsoft Australia Building Footprints is available, non-overlapping buildings from
the supplement are merged in using IoU-based deduplication (threshold 0.30).

OSM coverage at zoom 19 (~0.29 m/pixel) exceeds 98% for Carlton and Clayton based on
visual inspection of the annotated output image. The Overpass API is free and requires
no registration.

**Output columns:** `building_id`, `area_m2`, `lat`, `lon`, `source`

---

## 2. Roof Material, Colour, and Absorptance

For buildings without an OSM `roof:material` tag (>98% of Melbourne buildings), the
satellite tile covering each building centroid is loaded, the footprint polygon is
projected onto it, and mean HSV colour statistics are computed from the masked pixels.

Solar absorptance is estimated directly from HSV Value (brightness):

```
absorptance = 0.97 − 0.77 × V    (for achromatic surfaces, S < 0.15)
```

This linear model is calibrated to AS/NZS 4859.1 tabulated values for common Australian
roofing products (white V=1 → α=0.20; dark iron V=0.1 → α=0.90). For chromatic surfaces
a hue-specific floor is applied (red/terracotta ≥ 0.65; blue ≥ 0.70).

Absorptance uncertainty (±1σ) is propagated to Stage 2: ±0.08 for near-white and
near-black; ±0.12–0.15 for mid-tones.

**Coverage:** 92.1% of buildings classified. The 7.9% gap (483 in Carlton, 445 in
Clayton) arises from buildings outside the extent of downloaded satellite tiles.
These buildings carry `null` absorptance, not an imputed value.

**Output columns:** `roof_material`, `roof_colour`, `absorptance_estimate`,
`absorptance_uncertainty`, `classifier_confidence`

---

## 3. Orientation

`orientation_deg` is the compass bearing (0–360° clockwise from North) of the outward
normal to the longest edge of the building footprint polygon. This approximates the
dominant direction a roof slope faces.

The algorithm:
1. Scale all edges from degrees to metres using a latitude-adjusted coordinate transform.
2. Find the longest edge; compute its bearing from North using `atan2(east, north)`.
3. Rotate 90° clockwise for the outward normal.

**Limitation:** For L-shaped or irregular polygons the result may not correspond to the
actual ridge direction. When LiDAR is available, the pitch extractor computes a more
accurate `aspect_deg` from the fitted plane normal.

**Coverage:** 100% of buildings have `orientation_deg`.

---

## 4. Pitch

In the absence of LiDAR data, roof pitch is assigned from a lookup table based on
OSM `roof:shape` and `building:type` tags:

| Condition | Pitch |
|---|---|
| `roof:shape=flat` or commercial/office/retail type | 0° |
| 4+ storey buildings | 0° |
| Industrial/warehouse | 5° |
| Garage/carport/shed/school | 15° |
| Residential (typical) / gabled / hipped | 22.5° |
| Church/cathedral/mosque | 30° |

All pitch values carry `pitch_source="assumed"`. When ELVIS 1m LiDAR is available
for a suburb, the standalone pitch extractor (`tools/extract_pitch.py`) fits a
RANSAC plane to DSM points within each footprint and overwrites `pitch_deg` with
`pitch_source="lidar"`.

**Coverage:** 100% of buildings have `pitch_deg` and `pitch_source`.
`is_flat=True` for 8.7% of Carlton buildings.

---

## 5. Roof Surface Area

Actual roof surface area (inclined) is calculated from the footprint area and pitch:

```
roof_surface_area_m2 = area_m2 / cos(pitch_deg)
```

For pitch < 0.5° the areas are equal. A standard 22.5° pitch adds ~8.6% more area
than the footprint. This correction is applied before irradiance integration in Stage 2.

---

## 6. Output Schema

Each building record carries:

| Column | Type | Notes |
|---|---|---|
| `building_id` | str | OSM way/relation ID |
| `roof_id` | str | `{suburb}_{building_id}` |
| `area_m2` | float | Footprint area (planar) |
| `roof_surface_area_m2` | float | Inclined area = area_m2 / cos(pitch) |
| `lat`, `lon` | float | Centroid, EPSG:4326 |
| `source` | str | osm / vicmap / msft |
| `building_type` | str | OSM building tag |
| `levels` | int | OSM building:levels |
| `roof_material` | str | Classifier or OSM tag |
| `roof_colour` | str | Classifier colour class |
| `roof_shape` | str | OSM roof:shape tag |
| `pitch_deg` | float | Assumed (see §4) |
| `pitch_source` | str | "assumed" or "lidar" |
| `is_flat` | bool | pitch_deg < 5° |
| `orientation_deg` | float | 0–360° from N, footprint-derived |
| `absorptance_estimate` | float | Solar absorptance α (0–1) |
| `absorptance_uncertainty` | float | ±1σ |
| `classifier_confidence` | float | 0–1; 1.0 = OSM tag used |

Outputs: `data/output/stage1_{suburb}.parquet`, `.csv`, `.geojson`,
`_polygons.json` (polygon sidecar for pitch tool).

---

## 7. Assumptions and Limitations

1. **Pitch is assumed** for all buildings until LiDAR data is obtained. The typical 22.5°
   default is appropriate for Melbourne residential stock; sensitivity analysis at 15°,
   22.5°, and 30° is recommended for Stage 2/3 reporting.

2. **Orientation is footprint-derived**. For complex building shapes the longest-wall
   normal is a reasonable proxy, but ~20% of buildings may have a different dominant
   slope direction.

3. **Absorptance uncertainty is ±0.08–0.15 (1σ)**. The aggregate effect across a suburb
   averages out somewhat, but individual building estimates carry material uncertainty.

4. **OSM completeness** varies. Small outbuildings (sheds, carports < 10 m²) are
   excluded. Some recent constructions may not yet appear in OSM.

5. **Satellite imagery date** is determined by Google Maps Static API tile cache and is
   not controlled. Seasonal variation in vegetation shadows could affect pixel classification.

---

## 8. Validation

- 107 automated tests pass across the full pipeline (as of 2026-07-01).
- Carlton and Clayton annotated PNG images visually confirm polygon alignment with
  satellite footprints.
- Absorptance distribution for Carlton (mean 0.67, SD 0.11) is consistent with CSR VIC
  roof material statistics (metal 47.5%, concrete tile 17.5%, terracotta 15%).
