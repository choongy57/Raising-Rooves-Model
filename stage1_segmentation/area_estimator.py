"""
Roof area estimation for the Raising Rooves pipeline.

Converts pixel-count segmentation masks to real-world area (m²) using
Mercator projection mathematics. Aggregates results per suburb.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from shared.geo_utils import pixels_to_area_m2, tile_centre_latlon
from shared.logging_config import setup_logging

logger = setup_logging("area_estimator")


@dataclass
class RoofArea:
    """Area estimate for a single detected roof."""

    roof_id: str  # unique ID: {suburb}_{tile_x}_{tile_y}_seg{i}
    area_m2: float
    pixel_count: int
    tile_x: int
    tile_y: int
    lat: float  # centroid latitude
    lon: float  # centroid longitude
    material: str
    colour: str
    confidence: float


@dataclass
class SuburbAreaSummary:
    """Aggregated roof area statistics for a suburb."""

    suburb: str
    total_roof_area_m2: float
    num_roofs: int
    mean_roof_area_m2: float
    median_roof_area_m2: float
    material_breakdown: dict[str, float]  # material -> total m²


def estimate_roof_area(
    pixel_count: int,
    lat: float,
    zoom: int,
) -> float:
    """
    Convert a pixel count to real-world area in m².

    Args:
        pixel_count: Number of pixels in the roof mask.
        lat: Latitude of the tile centre (for Mercator correction).
        zoom: Zoom level used when downloading the tile.

    Returns:
        Roof area in square metres.
    """
    return pixels_to_area_m2(pixel_count, lat, zoom)


def estimate_tile_roofs(
    segments: list[dict],
    tile_x: int,
    tile_y: int,
    zoom: int,
    suburb_name: str,
) -> list[RoofArea]:
    """
    Estimate areas for all roof segments in a single tile.

    Args:
        segments: List of segment dicts with keys: pixel_count, centroid,
                  material, colour, confidence, segment_id.
        tile_x: Tile X coordinate.
        tile_y: Tile Y coordinate.
        zoom: Zoom level.
        suburb_name: Name of the suburb (for ID generation).

    Returns:
        List of RoofArea objects.
    """
    tile_lat, tile_lon = tile_centre_latlon(tile_x, tile_y, zoom)
    suburb_key = suburb_name.lower().replace(" ", "_")

    roof_areas = []
    for seg in segments:
        pixel_count = seg["pixel_count"]
        area_m2 = estimate_roof_area(pixel_count, tile_lat, zoom)

        roof_id = f"{suburb_key}_{tile_x}_{tile_y}_seg{seg['segment_id']}"

        roof_areas.append(
            RoofArea(
                roof_id=roof_id,
                area_m2=area_m2,
                pixel_count=pixel_count,
                tile_x=tile_x,
                tile_y=tile_y,
                lat=tile_lat,
                lon=tile_lon,
                material=seg.get("material", "unknown"),
                colour=seg.get("colour", "unknown"),
                confidence=seg.get("confidence", 0.0),
            )
        )

    logger.debug(
        "Tile (%d, %d): %d roofs, total area %.0f m²",
        tile_x,
        tile_y,
        len(roof_areas),
        sum(r.area_m2 for r in roof_areas),
    )

    return roof_areas


def aggregate_suburb_areas(
    roof_areas: list[RoofArea], suburb_name: str
) -> SuburbAreaSummary:
    """
    Aggregate individual roof areas into a suburb-level summary.

    Args:
        roof_areas: List of all RoofArea objects for the suburb.
        suburb_name: Name of the suburb.

    Returns:
        SuburbAreaSummary with totals and breakdowns.
    """
    if not roof_areas:
        logger.warning("No roofs found for suburb '%s'", suburb_name)
        return SuburbAreaSummary(
            suburb=suburb_name,
            total_roof_area_m2=0.0,
            num_roofs=0,
            mean_roof_area_m2=0.0,
            median_roof_area_m2=0.0,
            material_breakdown={},
        )

    areas = [r.area_m2 for r in roof_areas]

    # Material breakdown
    material_totals: dict[str, float] = {}
    for r in roof_areas:
        material_totals[r.material] = material_totals.get(r.material, 0.0) + r.area_m2

    summary = SuburbAreaSummary(
        suburb=suburb_name,
        total_roof_area_m2=sum(areas),
        num_roofs=len(areas),
        mean_roof_area_m2=float(np.mean(areas)),
        median_roof_area_m2=float(np.median(areas)),
        material_breakdown=material_totals,
    )

    logger.info(
        "Suburb '%s': %d roofs, total %.0f m², mean %.0f m², materials: %s",
        suburb_name,
        summary.num_roofs,
        summary.total_roof_area_m2,
        summary.mean_roof_area_m2,
        {k: f"{v:.0f} m²" for k, v in material_totals.items()},
    )

    return summary


def roof_areas_to_dataframe(roof_areas: list[RoofArea], suburb_name: str) -> pd.DataFrame:
    """
    Convert a list of RoofArea objects to a pandas DataFrame.

    This is the standard output format for Stage 1, saved as Parquet.
    """
    if not roof_areas:
        return pd.DataFrame(
            columns=["suburb", "roof_id", "area_m2", "pixel_count", "material",
                      "colour", "confidence", "lat", "lon"]
        )

    return pd.DataFrame(
        [
            {
                "suburb": suburb_name,
                "roof_id": r.roof_id,
                "area_m2": r.area_m2,
                "pixel_count": r.pixel_count,
                "material": r.material,
                "colour": r.colour,
                "confidence": r.confidence,
                "lat": r.lat,
                "lon": r.lon,
            }
            for r in roof_areas
        ]
    )
