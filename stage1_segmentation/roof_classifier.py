"""
Roof material and colour classifier for the Raising Rooves pipeline.

MVP approach: colour-based heuristic using mean RGB/HSV values of each
segmented roof region. Maps to broad categories based on known Victorian
roof material distributions (CSR data).

Future upgrade: fine-tune a small image classifier on labelled examples.
"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
from PIL import Image

from config.settings import ROOF_MATERIAL_PRIORS
from shared.logging_config import setup_logging

logger = setup_logging("roof_classifier")


class RoofMaterial(str, Enum):
    METAL_LIGHT = "metal_light"
    METAL_DARK = "metal_dark"
    TERRACOTTA = "terracotta"
    CONCRETE_TILE = "concrete_tile"
    OTHER = "other"


class RoofColour(str, Enum):
    WHITE = "white"
    LIGHT_GREY = "light_grey"
    DARK_GREY = "dark_grey"
    RED = "red"
    BROWN = "brown"
    BLUE = "blue"
    GREEN = "green"
    OTHER = "other"


@dataclass
class RoofClassification:
    """Classification result for a single roof segment."""

    material: RoofMaterial
    colour: RoofColour
    mean_rgb: tuple[float, float, float]
    mean_hsv: tuple[float, float, float]
    confidence: float  # 0.0 to 1.0


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """Convert RGB array (0-255) to HSV (H: 0-360, S: 0-1, V: 0-1)."""
    rgb_norm = rgb.astype(float) / 255.0
    r, g, b = rgb_norm[..., 0], rgb_norm[..., 1], rgb_norm[..., 2]

    cmax = np.maximum(np.maximum(r, g), b)
    cmin = np.minimum(np.minimum(r, g), b)
    delta = cmax - cmin

    # Hue
    h = np.zeros_like(delta)
    mask = delta > 0
    rm = mask & (cmax == r)
    gm = mask & (cmax == g)
    bm = mask & (cmax == b)
    h[rm] = 60.0 * (((g[rm] - b[rm]) / delta[rm]) % 6)
    h[gm] = 60.0 * (((b[gm] - r[gm]) / delta[gm]) + 2)
    h[bm] = 60.0 * (((r[bm] - g[bm]) / delta[bm]) + 4)

    # Saturation
    s = np.where(cmax > 0, delta / cmax, 0)

    # Value
    v = cmax

    return np.stack([h, s, v], axis=-1)


def _classify_by_hsv(mean_h: float, mean_s: float, mean_v: float) -> tuple[RoofMaterial, RoofColour, float]:
    """
    Classify roof material and colour from mean HSV values.

    Heuristic rules based on typical satellite imagery appearance of
    Melbourne roofing materials.
    """
    confidence = 0.5  # base confidence for heuristic

    # Very bright / white (high V, low S) → light metal or coated
    if mean_v > 0.75 and mean_s < 0.15:
        return RoofMaterial.METAL_LIGHT, RoofColour.WHITE, 0.7

    # Light grey (moderate-high V, low S)
    if mean_v > 0.5 and mean_s < 0.15:
        return RoofMaterial.CONCRETE_TILE, RoofColour.LIGHT_GREY, 0.6

    # Dark grey (low V, low S) → dark metal
    if mean_v < 0.35 and mean_s < 0.2:
        return RoofMaterial.METAL_DARK, RoofColour.DARK_GREY, 0.6

    # Red/brown hues (H: 0-30 or 330-360, moderate S) → terracotta
    if (mean_h < 30 or mean_h > 330) and mean_s > 0.2:
        if mean_v > 0.4:
            return RoofMaterial.TERRACOTTA, RoofColour.RED, 0.65
        else:
            return RoofMaterial.TERRACOTTA, RoofColour.BROWN, 0.55

    # Blue hues (H: 200-260) → colorbond blue
    if 200 < mean_h < 260 and mean_s > 0.15:
        return RoofMaterial.METAL_DARK, RoofColour.BLUE, 0.5

    # Green hues (H: 80-160)
    if 80 < mean_h < 160 and mean_s > 0.15:
        return RoofMaterial.METAL_DARK, RoofColour.GREEN, 0.5

    # Default: mid-tone metal
    if mean_v < 0.5:
        return RoofMaterial.METAL_DARK, RoofColour.DARK_GREY, 0.4
    return RoofMaterial.OTHER, RoofColour.OTHER, 0.3


def classify_roof(
    tile_image: np.ndarray,
    mask: np.ndarray,
    segment_id: int = 0,
) -> RoofClassification:
    """
    Classify a single roof segment by material and colour.

    Args:
        tile_image: Original satellite tile as RGB numpy array (H, W, 3).
        mask: Binary mask for this specific segment (H, W), True = roof.
        segment_id: ID for logging purposes.

    Returns:
        RoofClassification with material, colour, and confidence.
    """
    # Extract pixels under the mask
    roof_pixels = tile_image[mask]

    if len(roof_pixels) == 0:
        logger.warning("Segment %d: empty mask, cannot classify", segment_id)
        return RoofClassification(
            material=RoofMaterial.OTHER,
            colour=RoofColour.OTHER,
            mean_rgb=(0, 0, 0),
            mean_hsv=(0, 0, 0),
            confidence=0.0,
        )

    # Compute mean RGB
    mean_rgb = tuple(float(v) for v in roof_pixels.mean(axis=0))

    # Compute mean HSV
    hsv_pixels = _rgb_to_hsv(roof_pixels.reshape(-1, 1, 3)).reshape(-1, 3)
    mean_hsv = tuple(float(v) for v in hsv_pixels.mean(axis=0))

    # Classify
    material, colour, confidence = _classify_by_hsv(mean_hsv[0], mean_hsv[1], mean_hsv[2])

    logger.debug(
        "Segment %d: material=%s, colour=%s (conf=%.2f), RGB=(%.0f,%.0f,%.0f)",
        segment_id,
        material.value,
        colour.value,
        confidence,
        *mean_rgb,
    )

    return RoofClassification(
        material=material,
        colour=colour,
        mean_rgb=mean_rgb,
        mean_hsv=mean_hsv,
        confidence=confidence,
    )
