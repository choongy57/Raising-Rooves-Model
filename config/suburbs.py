"""
Melbourne suburb definitions for the Raising Rooves pipeline.

Each suburb has: name, ABS SA2 code (where available), centroid (lat, lon),
and bounding box (south, west, north, east) in EPSG:4326.

Starting with 6 test suburbs spanning inner, middle, and outer Melbourne
across residential, commercial, and industrial zones.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Suburb:
    name: str
    sa2_code: str  # ABS SA2 code, empty string if unknown
    centroid: tuple[float, float]  # (lat, lon)
    bbox: tuple[float, float, float, float]  # (south, west, north, east)
    zone_type: str  # "residential", "industrial", "commercial", "mixed"


# ── Test Suburbs ─────────────────────────────────────────────────────────────
# Chosen to cover different zone types, distances from CBD, and roof profiles.

SUBURBS = {
    "richmond": Suburb(
        name="Richmond",
        sa2_code="206041122",
        centroid=(-37.8183, 144.9981),
        bbox=(-37.8300, 144.9850, -37.8050, 145.0150),
        zone_type="mixed",
    ),
    "carlton": Suburb(
        name="Carlton",
        sa2_code="206041118",
        centroid=(-37.7998, 144.9667),
        bbox=(-37.8100, 144.9550, -37.7900, 144.9780),
        zone_type="residential",
    ),
    "footscray": Suburb(
        name="Footscray",
        sa2_code="206031098",
        centroid=(-37.8000, 144.8992),
        bbox=(-37.8120, 144.8850, -37.7880, 144.9130),
        zone_type="mixed",
    ),
    "box_hill": Suburb(
        name="Box Hill",
        sa2_code="206051133",
        centroid=(-37.8190, 145.1218),
        bbox=(-37.8310, 145.1070, -37.8070, 145.1370),
        zone_type="residential",
    ),
    "dandenong": Suburb(
        name="Dandenong",
        sa2_code="206071185",
        centroid=(-37.9870, 145.2150),
        bbox=(-38.0000, 145.1950, -37.9740, 145.2350),
        zone_type="industrial",
    ),
    "tullamarine": Suburb(
        name="Tullamarine",
        sa2_code="206021068",
        centroid=(-37.7000, 144.8800),
        bbox=(-37.7200, 144.8550, -37.6800, 144.9050),
        zone_type="industrial",
    ),
    "clayton": Suburb(
        name="Clayton",
        sa2_code="206061166",
        centroid=(-37.9150, 145.1220),
        bbox=(-37.9270, 145.1060, -37.9030, 145.1380),
        zone_type="mixed",  # Monash University + residential
    ),
}


def get_suburb(name: str) -> Suburb:
    """Look up a suburb by name (case-insensitive, spaces replaced with underscores)."""
    key = name.lower().replace(" ", "_")
    if key not in SUBURBS:
        available = ", ".join(s.name for s in SUBURBS.values())
        raise ValueError(f"Unknown suburb '{name}'. Available: {available}")
    return SUBURBS[key]


def list_suburbs() -> list[str]:
    """Return list of available suburb names."""
    return [s.name for s in SUBURBS.values()]
