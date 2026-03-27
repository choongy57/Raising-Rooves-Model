"""
Data and environment validation helpers for the Raising Rooves pipeline.

All validation functions return True on success or raise ValueError with
a clear message on failure.
"""

import os
from pathlib import Path

from PIL import Image

from config.settings import MELBOURNE_BBOX


def validate_bbox(bbox: tuple[float, float, float, float]) -> bool:
    """
    Check that a bounding box is valid and within the greater Melbourne area.

    Args:
        bbox: (south, west, north, east) in EPSG:4326.

    Raises:
        ValueError: If bbox is malformed or outside Melbourne.
    """
    if len(bbox) != 4:
        raise ValueError(f"Bounding box must have 4 values (south, west, north, east), got {len(bbox)}")

    south, west, north, east = bbox
    if south >= north:
        raise ValueError(f"South ({south}) must be less than north ({north})")
    if west >= east:
        raise ValueError(f"West ({west}) must be less than east ({east})")

    # Check within greater Melbourne bounds (with margin)
    mel_south, mel_west, mel_north, mel_east = MELBOURNE_BBOX
    margin = 0.5  # degrees
    if south < mel_south - margin or north > mel_north + margin:
        raise ValueError(f"Latitude ({south}, {north}) outside Melbourne range")
    if west < mel_west - margin or east > mel_east + margin:
        raise ValueError(f"Longitude ({west}, {east}) outside Melbourne range")

    return True


def validate_tile(path: Path, expected_size: int = 640) -> bool:
    """
    Check that a downloaded tile image is valid.

    Args:
        path: Path to the tile image file.
        expected_size: Expected width/height in pixels.

    Raises:
        ValueError: If file is missing, corrupt, wrong size, or all-black.
    """
    if not path.exists():
        raise ValueError(f"Tile file does not exist: {path}")

    try:
        img = Image.open(path)
        img.verify()
    except Exception as e:
        raise ValueError(f"Tile image is corrupt: {path} — {e}")

    # Re-open after verify (verify closes the file)
    img = Image.open(path)
    w, h = img.size
    if w != expected_size or h != expected_size:
        raise ValueError(f"Tile size {w}x{h} does not match expected {expected_size}x{expected_size}: {path}")

    # Check not all-black (common error tile indicator)
    extrema = img.convert("L").getextrema()
    if extrema == (0, 0):
        raise ValueError(f"Tile is all-black (likely an error tile): {path}")

    return True


def validate_env_vars(required: list[str]) -> dict[str, str]:
    """
    Check that all required environment variables are set and non-empty.

    Args:
        required: List of environment variable names.

    Returns:
        Dict mapping variable names to their values.

    Raises:
        ValueError: If any required variable is missing or empty.
    """
    values = {}
    missing = []
    for var in required:
        val = os.getenv(var, "").strip()
        if not val:
            missing.append(var)
        else:
            values[var] = val

    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Set them in your .env file (see .env.example)."
        )

    return values
