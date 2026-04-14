"""
SAM3 segmentation wrapper for the Raising Rooves pipeline.

This module provides the interface for running SAM3 (Segment Anything 3)
on satellite tiles to detect roof segments. SAM3 uses text-prompted
segmentation — we prompt with "roof" for zero-shot roof detection.

IMPORTANT: SAM3 requires NVIDIA CUDA (12.6+). Since the local machine has
an AMD GPU, this code is designed to run on Google Colab. The Colab notebook
at notebooks/colab_sam3_inference.ipynb orchestrates the full batch process.

Locally, this module is used to:
  - Load and process mask results that were generated on Colab
  - Provide utility functions for working with segmentation outputs
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from shared.logging_config import setup_logging

logger = setup_logging("sam_segmenter")


@dataclass
class RoofSegment:
    """A single detected roof segment."""

    segment_id: int
    pixel_count: int
    bbox: tuple[int, int, int, int]  # (x_min, y_min, x_max, y_max)
    centroid: tuple[float, float]  # (cx, cy) in pixel coordinates
    confidence: float


@dataclass
class SegmentationResult:
    """Result of segmenting a single tile."""

    tile_path: Path
    mask: np.ndarray  # binary mask (H, W) — True where roof detected
    segments: list[RoofSegment] = field(default_factory=list)


# ── Functions for Colab (run on GPU) ─────────────────────────────────────────


def load_sam3_model():
    """
    Load the SAM3 model and processor. Runs on Colab with CUDA.

    Returns:
        Tuple of (model, processor).

    Raises:
        ImportError: If sam3 package is not installed (expected on local machine).
    """
    try:
        from sam3.model_builder import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor
    except ImportError:
        raise ImportError(
            "SAM3 is not installed locally. Run segmentation on Google Colab instead.\n"
            "See: notebooks/colab_sam3_inference.ipynb"
        )

    logger.info("Loading SAM3 model...")
    model = build_sam3_image_model()
    processor = Sam3Processor(model)
    logger.info("SAM3 model loaded successfully.")
    return model, processor


def segment_tile_sam3(tile_path: Path, model, processor) -> SegmentationResult:
    """
    Run SAM3 on a single tile to detect roofs. Runs on Colab with CUDA.

    Args:
        tile_path: Path to the satellite tile image.
        model: Loaded SAM3 model.
        processor: SAM3 processor instance.

    Returns:
        SegmentationResult with mask and per-segment metadata.
    """
    image = Image.open(tile_path).convert("RGB")
    inference_state = processor.set_image(image)
    output = processor.set_text_prompt(state=inference_state, prompt="roof")

    masks = output["masks"]  # (N, H, W) tensor
    boxes = output["boxes"]  # (N, 4) tensor
    scores = output["scores"]  # (N,) tensor

    # Convert to numpy
    if hasattr(masks, "cpu"):
        masks_np = masks.cpu().numpy()
        boxes_np = boxes.cpu().numpy()
        scores_np = scores.cpu().numpy()
    else:
        masks_np = np.array(masks)
        boxes_np = np.array(boxes)
        scores_np = np.array(scores)

    # Build combined binary mask and per-segment metadata
    h, w = masks_np.shape[1], masks_np.shape[2] if masks_np.ndim == 3 else (0, 0)
    combined_mask = np.zeros((h, w), dtype=bool)
    segments = []

    for i in range(len(scores_np)):
        mask_i = masks_np[i] > 0.5
        pixel_count = int(mask_i.sum())
        if pixel_count < 50:  # skip tiny fragments
            continue

        combined_mask |= mask_i

        # Compute centroid
        ys, xs = np.where(mask_i)
        cx, cy = float(xs.mean()), float(ys.mean())

        box = tuple(int(v) for v in boxes_np[i])

        segments.append(
            RoofSegment(
                segment_id=i,
                pixel_count=pixel_count,
                bbox=box,
                centroid=(cx, cy),
                confidence=float(scores_np[i]),
            )
        )

    logger.debug("Segmented %s: %d roof segments found", tile_path.name, len(segments))
    return SegmentationResult(tile_path=tile_path, mask=combined_mask, segments=segments)


# ── Functions for Local (load Colab results) ─────────────────────────────────


def save_segmentation_result(result: SegmentationResult, output_dir: Path) -> tuple[Path, Path]:
    """
    Save a segmentation result as a mask PNG and a JSON sidecar.

    Args:
        result: SegmentationResult to save.
        output_dir: Directory to save the files to.

    Returns:
        Tuple of (mask_path, json_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = result.tile_path.stem

    # Save binary mask as PNG (white = roof, black = background)
    mask_img = Image.fromarray((result.mask * 255).astype(np.uint8), mode="L")
    mask_path = output_dir / f"{stem}_mask.png"
    mask_img.save(mask_path)

    # Save segment metadata as JSON
    metadata = {
        "tile": result.tile_path.name,
        "total_roof_pixels": int(result.mask.sum()),
        "num_segments": len(result.segments),
        "segments": [
            {
                "id": s.segment_id,
                "pixel_count": s.pixel_count,
                "bbox": list(s.bbox),
                "centroid": list(s.centroid),
                "confidence": s.confidence,
            }
            for s in result.segments
        ],
    }
    json_path = output_dir / f"{stem}_meta.json"
    json_path.write_text(json.dumps(metadata, indent=2))

    return mask_path, json_path


def load_segmentation_result(mask_path: Path, json_path: Path) -> SegmentationResult:
    """
    Load a segmentation result from saved mask PNG and JSON sidecar.

    Use this locally to process results generated on Colab.

    Args:
        mask_path: Path to the binary mask PNG.
        json_path: Path to the JSON metadata file.

    Returns:
        Reconstructed SegmentationResult.
    """
    # Load mask
    mask_img = Image.open(mask_path).convert("L")
    mask = np.array(mask_img) > 127  # binary

    # Load metadata
    metadata = json.loads(json_path.read_text())

    segments = [
        RoofSegment(
            segment_id=s["id"],
            pixel_count=s["pixel_count"],
            bbox=tuple(s["bbox"]),
            centroid=tuple(s["centroid"]),
            confidence=s["confidence"],
        )
        for s in metadata["segments"]
    ]

    # Reconstruct tile path from metadata
    tile_name = metadata.get("tile", mask_path.stem.replace("_mask", ""))
    tile_path = Path(tile_name)  # relative — actual tile may be elsewhere

    return SegmentationResult(tile_path=tile_path, mask=mask, segments=segments)
