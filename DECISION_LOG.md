# Decision Log — Raising Rooves Roof Attribution Pipeline

Each entry records a method or source choice, why it was made, and what was rejected.

---

## 2026-07-01 — Footprint source: OSM Overpass + VicMap/Microsoft supplement

**Decision:** Use OpenStreetMap Overpass API as the primary footprint source, merging
with a locally-indexed GeoPackage (VicMap or Microsoft AU footprints) when available.

**Why:** OSM requires no download, no API key, and covers all of Victoria via a single
HTTP request per suburb. The supplement adds ~2% more buildings in outer suburbs where
OSM coverage is thinner.

**Rejected:**
- *Microsoft Building Footprints alone* — 845 MB download with no roof tag metadata.
- *VicMap BUILDING_POLYGON alone* — authoritative but manual download per region, no
  roof material/shape tags, refreshed only every 2–3 years.
- *Overture Maps* — evaluated but schema not yet stable enough.

**Coverage achieved:** Carlton 6177 buildings, Clayton 8025 buildings. OSM coverage ~98%
of visible structures at zoom 19.

---

## 2026-07-01 — Material/colour/albedo: HSV pixel classifier

**Decision:** Use mean HSV values from satellite tile pixels within each building footprint
polygon; map to material class and solar absorptance via rule-based thresholds calibrated
to Melbourne Colorbond and terracotta distributions (CSR VIC priors).

**Why:** Fast, fully local, no per-query API cost, and sufficient for suburb-scale
aggregate analysis. The absorptance estimate has explicit uncertainty (±0.08–0.15)
that propagates into Stage 2/3 sensitivity analysis.

**Rejected:**
- *Gemini Vision API* — available (key in .env) but adds latency, per-query cost, and
  doesn't produce a numeric absorptance directly. Could improve categorical accuracy but
  not warranted without a labelled validation set for Melbourne roofs.
- *Fine-tuned image classifier* — would require 500–1000 labelled Melbourne roof images
  we don't have. Future upgrade path.

**Coverage:** 92.1% of buildings classified (7.9% outside downloaded tile bounds).
Unclassified buildings have null absorptance_estimate — flagged explicitly, not imputed.

---

## 2026-07-01 — Orientation: normal to longest footprint wall

**Decision:** Compute orientation_deg as the bearing (0–360° clockwise from North) of
the outward normal to the longest edge of the building footprint polygon.

**Why:** Requires no additional data. The longest wall approximates the building's
primary axis; its normal approximates the dominant roof slope direction. Sufficient for
suburb-scale aggregate statistics (N-S vs E-W bias analysis).

**Rejected:**
- *OSM roof:direction tag* — present on < 1% of Melbourne buildings.
- *LiDAR-derived aspect* — pitch_extractor.py already computes aspect_deg when a DSM
  is available. Will supersede this estimate for LiDAR-covered buildings.
- *OBB (oriented bounding box) major axis* — more robust for irregular polygons but
  adds dependency on scipy; the longest-edge method is adequate and simpler.

**Limitation:** For L-shaped or irregular buildings the longest edge may not represent
the main roof ridge. Flag: orientation_source = "footprint" (not yet in schema, add if
LiDAR source is integrated).

---

## 2026-07-01 — Pitch: assumed from building type / OSM roof:shape tag

**Decision:** Use `_assumed_pitch_deg()` which maps OSM roof:shape and building_type
to typical Melbourne pitch values (0° flat, 5° industrial, 15° shallow, 22.5° typical
residential, 30° heritage/church). Flag all as pitch_source="assumed".

**Why:** No LiDAR/DSM file is currently available for Carlton or Clayton. The ELVIS
1m LiDAR download covers these suburbs but requires manual registration + download.
Stuart's guidance: demonstrate capability, not perfection — assumed typical values are
defensible for suburb-aggregate analysis.

**Pitch defaults used:**
- flat: 0° (commercial, office, retail, hospital, 4+ storey, roof:shape=flat)
- low (5°): industrial, warehouse, factory
- shallow (15°): garage, carport, shed, school
- typical (22.5°): residential "yes", gabled, hipped
- steep (30°): church, cathedral, mosque

**Rejected:**
- *COP30 coarse DSM (30 m)* — too coarse for individual building pitch; would produce
  terrain slope not roof pitch. Available via OpenTopography but not useful here.
- *Google Solar API pitch field* — not publicly documented/accessible without Business
  agreement.

**When LiDAR becomes available:** run `python -m tools.extract_pitch --suburb Carlton
--dsm-file <path>` to write pitch_deg and pitch_source="lidar" to a merged parquet.

---

## 2026-07-01 — Roof surface area: area_m2 / cos(pitch)

**Decision:** Compute `roof_surface_area_m2 = area_m2 / cos(radians(pitch_deg))` for
all non-flat roofs. For pitch < 0.5° treat as flat (surface area = footprint area).

**Why:** The irradiance model (Stage 2) needs the actual inclined area to compute absorbed
solar energy. A 22.5° pitch roof has 8.6% more surface area than its footprint. This is
a small but non-negligible correction for the aggregate across thousands of buildings.

**Note:** Because pitch is assumed for all buildings currently, roof_surface_area_m2
carries the same uncertainty as pitch_deg. The sensitivity is low: ±7.5° pitch error
at 22.5° nominal → ±4% area error.

---

## 2026-07-01 — Output formats: Parquet + CSV + GeoJSON + polygon sidecar

**Decision:** Stage 1 writes four outputs per suburb:
- `stage1_{suburb}.parquet` — typed, compressed, fast for downstream stages
- `stage1_{suburb}.csv` — human-readable, Excel-compatible
- `stage1_{suburb}.geojson` — full geometry + all attributes; loadable in QGIS/Mapbox
- `stage1_{suburb}_polygons.json` — ordered polygon list for DSM pitch extraction tool

**Why:** GeoJSON enables spatial QA in any GIS tool without extra processing. Parquet is
the canonical format for Stage 2/3. CSV is for team members without Python.

---

## 2026-07-01 — Absorptance model: AS/NZS 4859.1 linear fit on HSV Value

**Decision:** `absorptance = 0.97 − 0.77 × V` for achromatic surfaces (S < 0.15);
hue-specific floor applied for chromatic surfaces (red/terracotta ≥ 0.65, blue ≥ 0.70).

**Why:** Grounded in AS/NZS 4859.1 tabulated absorptance values for common Australian
roofing products. White (V=1) → 0.20 matches a typical cool roof target. Dark iron (V=0.1)
→ 0.90 matches Colorbond Night Sky measurements.

**Uncertainty:** ±0.08 for near-white and near-black; ±0.12–0.15 for mid-tones.
These are ±1σ estimates, not worst-case bounds.

**Rejected:**
- *Lookup table on classifier material label* — requires a labelled dataset; intermediate
  label adds error. Direct HSV → absorptance is more transparent and calibratable.
