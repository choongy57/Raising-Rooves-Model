# Vicmap Elevation / Victoria LiDAR 1 m Coverage

Date: 2026-04-28

## Question

What official Vicmap Elevation / Victoria LiDAR products exist, what do they
contain, what coverage/licensing/update notes are stated, and are they suitable
for estimating residential roof pitch and building height?

## Primary official sources

- Vicmap Elevation:
  https://www.land.vic.gov.au/maps-and-spatial/spatial-data/vicmap-catalogue/vicmap-elevation
- Vicmap Elevation 1m DEM:
  https://www.land.vic.gov.au/maps-and-spatial/spatial-data/vicmap-catalogue/vicmap-elevation/1m-dem
- Elevation data:
  https://www.land.vic.gov.au/maps-and-spatial/imagery/elevation-data
- Digital Twin Victoria LiDAR 2021-24:
  https://www.land.vic.gov.au/maps-and-spatial/imagery/elevation-data/major-lidar-projects/digital-twin-victoria-lidar-2021-24
- How to access spatial data:
  https://www.land.vic.gov.au/maps-and-spatial/spatial-data/how-to-access-spatial-data
- Vicmap Elevation 1m DEM Footprints:
  https://discover.data.vic.gov.au/dataset/vicmap-elevation-1m-digital-elevation-model-dem-footprints

## Concise findings

### 1. Product types officially described

- `Vicmap Elevation` groups products into:
  - `Elevation Surfaces`: gridded surfaces such as DEMs.
  - `Elevation Point Clouds`: LiDAR point clouds.
  - `Elevation Features`: contours, spot heights, cliffs, embankments, and
    related vector features.
- The core statewide LiDAR archive products are:
  - `Vicmap Elevation - LiDAR DEMs Collection`: ground-surface DEM rasters.
  - `Vicmap Elevation - LiDAR Points Collection`: LiDAR point clouds.
  - `Vicmap Elevation 1m DEM`: a 1 m mosaic web-service product built from the
    LiDAR DEM archive.
- `Digital Twin Victoria LiDAR 2021-24` states that project outputs include:
  - `DEM 1 m`
  - `DHM 1 m`
  - `DSM 1 m`
  - point cloud in `LAZ 1.4`

### 2. Resolution, coverage, currency, accuracy

- `Vicmap Elevation - LiDAR DEMs Collection`
  - Resolution: `50 cm to 5 m`
  - Currency: `2007 to present`
  - Vertical accuracy: `+/-50 cm RMSE to +/-10 cm RMSE`
  - Coverage: `about 60% of Victoria`, `over 99% of populated areas`
- `Vicmap Elevation - LiDAR Points Collection`
  - Point density: `2 to 24 points/m2`
  - Currency: `2007 to present`
  - Vertical accuracy: `+/-50 cm RMSE to +/-10 cm RMSE`
  - Coverage: `about 60% of Victoria`, `over 99% of populated areas`
- `Vicmap Elevation 1m DEM`
  - Product type: mosaic of best available `ground surface` DEM data
  - Inclusion rule: only source datasets meeting `1 m resolution` and
    `+/-10 cm RMSE vertical accuracy`
  - Currency: `2009 to present`
  - Coverage: `about 60% of Victoria`
  - CRS/datum: `GDA2020`, `AHD`
- `Digital Twin Victoria LiDAR 2021-24`
  - Added extent: `>60,000 km2`
  - Point density: `8 to 16 first-return points/m2`
  - Products: `DEM 1 m`, `DHM 1 m`, `DSM 1 m`
  - Vertical accuracy: `10 cm RMSE`
  - Coverage statement: project intended to represent `99% of Victoria's population`
    and `over 95% of buildings` by high-accuracy terrain and point-cloud data

### 3. Coverage and update notes

- The `Vicmap 1m DEM Footprints` dataset is the official open coverage index for
  the 1 m DEM mosaic. It represents the boundaries of the source LiDAR DEM
  datasets used in the 1 m DEM.
- The footprints dataset is openly downloadable and exposes `WMS` and `WFS`.
- The footprints dataset on DataVic shows:
  - `Last updated: 2026-04-08`
  - `Update frequency: Irregular`
- The 1 m DEM page says:
  - gaps exist because LiDAR capture depends on purchase-partner investment via
    the Coordinated Imagery Program
  - when repeat coverage occurs, the latest qualifying data is used
  - the 1 m DEM is updated whenever new qualifying data becomes available, with
    timing described as sporadic

### 4. Licensing and access

- `Vicmap Elevation - LiDAR DEMs Collection`: licensed access via DALA / VAR /
  DSP; some project-specific exceptions are available on ELVIS under
  `CC BY-NC 4.0`
- `Vicmap Elevation - LiDAR Points Collection`: same licensing model as above;
  some project-specific exceptions on ELVIS under `CC BY-NC 4.0`
- `Vicmap Elevation 1m DEM`: licensed web-service access for government users;
  private/public users are directed to DSPs to access the source data
- `Vicmap Elevation 1m DEM Footprints`: open data under `CC BY 4.0`
- `Vicmap 10m DEM` and `10m-20m contours`: open data, but too coarse for roof
  modelling use

## Suitability for this project

### Directly stated by the sources

- The `Vicmap 1m DEM` is a `ground surface` product.
- The LiDAR archive also includes `point clouds`, and major modern projects can
  produce `DSM` and `DHM` in addition to `DEM`.

### Inference for roof pitch and building height

- `Vicmap 1m DEM` alone is **not suitable** for residential roof pitch or
  building height estimation, because it models bare/ground terrain rather than
  roof surfaces.
- `Vicmap LiDAR Points` are **potentially suitable** for roof pitch estimation
  and building height estimation where local point density and building returns
  are adequate. This is the strongest official Vicmap Elevation source for your
  current pipeline because it preserves above-ground geometry.
- A project-specific `DSM 1 m` is also **potentially suitable** for roof pitch
  and height estimation, but DSM availability appears tied to specific LiDAR
  projects rather than the general public `Vicmap 1m DEM` product.
- `Contours` are **not suitable** for residential roof pitch or building height
  work.

## Practical recommendation

- Use the open `Vicmap 1m DEM Footprints` dataset first to confirm whether a
  suburb has 1 m coverage and to inspect source survey dates.
- For actual roof-pitch extraction, prefer:
  1. `Vicmap Elevation - LiDAR Points Collection`, or
  2. project-specific `DSM 1 m` / `DHM 1 m` where available.
- Do not treat the public `Vicmap 1m DEM` ground-surface mosaic as a roof DSM.

## Relevance to Raising Rooves

- This confirms that the official Vicmap 1 m coverage source is useful for
  coverage discovery and terrain context, but not by itself for roof geometry.
- For `tools.extract_pitch`, the best-fit official Victorian source is the
  licensed LiDAR point cloud archive or project-specific DSM products from the
  Coordinated Imagery Program / Digital Twin Victoria LiDAR work.

## Next steps

1. Check whether target suburbs fall inside the open `1m DEM Footprints`
   dataset.
2. If they do, use the footprint attributes to identify source survey dates.
3. Request the paired `LiDAR Points` or project-specific `DSM` tiles for those
   footprints rather than relying on the DEM mosaic.
