from pathlib import Path

from PIL import Image

from stage1_segmentation.gemini_osm_experiment import (
    CropContext,
    _extract_json_object,
    normalise_assessment,
)


def test_extract_json_object_strips_markdown_fence():
    raw = """```json
{"roof_visible": true, "confidence": 0.5}
```"""

    assert _extract_json_object(raw) == {"roof_visible": True, "confidence": 0.5}


def test_normalise_assessment_clamps_and_defaults_values():
    crop = CropContext(
        image=Image.new("RGB", (100, 80)),
        tile_path=Path("tile.png"),
        crop_box=(1, 2, 101, 82),
        osm_polygon_crop_px=[[0, 0], [90, 0], [90, 70], [0, 70]],
    )
    raw = {
        "roof_visible": True,
        "usable_for_stage1": True,
        "image_quality": "clear",
        "occlusion_fraction": 1.5,
        "boundary_quality": "bad-value",
        "visible_roof_fraction": "0.75",
        "boundary_confidence": 0.8,
        "roof_colour": "Light Grey",
        "roof_colour_confidence": 0.9,
        "roof_material": "metal",
        "roof_material_confidence": 0.7,
        "material_evidence": "ribbed lines",
        "roof_shape": "gable",
        "roof_shape_confidence": 0.6,
        "pitch_observable": True,
        "pitch_class": "medium",
        "pitch_confidence": 0.5,
        "pitch_deg_estimate": 22.567,
        "confidence": 2,
        "pitch_basis": "ridge geometry",
        "qa_action": "accept with warning",
        "suggested_boundary_polygon_px": [[-10, 5], [50, 0], [200, 90]],
        "quality_flags": ["shadow", "bad flag", "shadow"],
        "evidence": "visible ridge line",
        "warnings": "single warning",
    }

    result = normalise_assessment("b1", raw, crop, "gemini-test")

    assert result.boundary_quality == "unclear"
    assert result.roof_colour == "light_grey"
    assert result.occlusion_fraction == 1.0
    assert result.visible_roof_fraction == 0.75
    assert result.confidence == 1.0
    assert result.pitch_deg_estimate == 22.57
    assert result.material_evidence == "ribbed_lines"
    assert result.pitch_basis == "ridge_geometry"
    assert result.qa_action == "needs_manual_review"
    assert result.quality_flags == ["shadow"]
    assert result.qa_score < 1.0
    assert result.suggested_boundary_polygon_px == [[0, 5], [50, 0], [99, 79]]
    assert result.warnings == ["single warning"]


def test_non_flat_visual_pitch_routes_to_dsm_even_with_number():
    crop = CropContext(
        image=Image.new("RGB", (100, 80)),
        tile_path=Path("tile.png"),
        crop_box=(1, 2, 101, 82),
        osm_polygon_crop_px=[[0, 0], [90, 0], [90, 70], [0, 70]],
    )
    raw = {
        "roof_visible": True,
        "usable_for_stage1": True,
        "image_quality": "clear",
        "occlusion_fraction": 0,
        "boundary_quality": "matches_osm",
        "visible_roof_fraction": 1,
        "boundary_confidence": 0.9,
        "roof_colour": "dark_grey",
        "roof_colour_confidence": 0.9,
        "roof_material": "tile",
        "roof_material_confidence": 0.8,
        "material_evidence": "tile_pattern",
        "roof_shape": "hip",
        "roof_shape_confidence": 0.8,
        "pitch_observable": True,
        "pitch_class": "medium",
        "pitch_confidence": 0.7,
        "pitch_deg_estimate": 20,
        "pitch_basis": "ridge_geometry",
        "confidence": 0.9,
        "qa_action": "accept",
        "suggested_boundary_polygon_px": [],
        "quality_flags": [],
        "evidence": "visible hip roof",
        "warnings": [],
    }

    result = normalise_assessment("b2", raw, crop, "gemini-test")

    assert result.qa_action == "needs_dsm"
    assert result.usable_for_stage1 is True
