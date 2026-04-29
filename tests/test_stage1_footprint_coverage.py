import json

from stage1_segmentation.building_footprint_segmenter import (
    _load_local_footprints,
    _polygon_intersects_bbox,
)


def test_polygon_intersection_keeps_boundary_crossing_footprint():
    polygon = [
        [144.9990, -37.0005],
        [145.0005, -37.0005],
        [145.0005, -37.0015],
        [144.9990, -37.0015],
        [144.9990, -37.0005],
    ]

    assert _polygon_intersects_bbox(
        polygon_latlon=polygon,
        south=-37.0020,
        west=145.0000,
        north=-37.0000,
        east=145.0020,
    )


def test_local_loader_keeps_intersecting_polygon_with_centroid_outside_bbox(tmp_path):
    feature = {
        "type": "Feature",
        "properties": {"id": "edge-building"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [144.9980, -37.0005],
                    [145.0002, -37.0005],
                    [145.0002, -37.0015],
                    [144.9980, -37.0015],
                    [144.9980, -37.0005],
                ]
            ],
        },
    }
    path = tmp_path / "footprints.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))

    footprints = _load_local_footprints(
        local_file=path,
        south=-37.0020,
        west=145.0000,
        north=-37.0000,
        east=145.0020,
        tile_centre_lat=-37.0010,
        tile_centre_lon=145.0010,
        zoom=19,
    )

    assert [footprint.building_id for footprint in footprints] == ["edge-building"]

