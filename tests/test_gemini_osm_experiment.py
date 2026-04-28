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
        "boundary_quality": "bad-value",
        "roof_colour": "Light Grey",
        "roof_material": "metal",
        "roof_shape": "gable",
        "pitch_class": "medium",
        "pitch_deg_estimate": 22.567,
        "confidence": 2,
        "suggested_boundary_polygon_px": [[-10, 5], [50, 0], [200, 90]],
        "warnings": "single warning",
    }

    result = normalise_assessment("b1", raw, crop, "gemini-test")

    assert result.boundary_quality == "unclear"
    assert result.roof_colour == "light_grey"
    assert result.confidence == 1.0
    assert result.pitch_deg_estimate == 22.57
    assert result.suggested_boundary_polygon_px == [[0, 5], [50, 0], [99, 79]]
    assert result.warnings == ["single warning"]

