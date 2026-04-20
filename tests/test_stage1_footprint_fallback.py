from pathlib import Path

from stage1_segmentation.building_footprint_segmenter import BuildingFootprint
from stage1_segmentation.pipeline import _query_pipeline_footprints


def _footprint(building_id: str, source: str) -> BuildingFootprint:
    return BuildingFootprint(
        building_id=building_id,
        area_m2=100.0,
        polygon_latlon=[
            [145.0, -37.0],
            [145.001, -37.0],
            [145.001, -37.001],
            [145.0, -37.001],
            [145.0, -37.0],
        ],
        source=source,
    )


def test_merge_file_falls_back_to_local_when_osm_fails(monkeypatch):
    local_file = Path("data/raw/footprints/buildings_index.gpkg")
    calls: list[Path | None] = []

    def fake_query_buildings_in_bbox(
        south: float,
        west: float,
        north: float,
        east: float,
        local_file: Path | None = None,
    ) -> list[BuildingFootprint]:
        calls.append(local_file)
        if local_file is None:
            raise RuntimeError("Overpass API failed after 3 attempts: 406")
        return [_footprint("local-1", "msft")]

    monkeypatch.setattr(
        "stage1_segmentation.pipeline.query_buildings_in_bbox",
        fake_query_buildings_in_bbox,
    )

    result = _query_pipeline_footprints(
        south=-37.92782,
        west=145.10485,
        north=-37.90208,
        east=145.13884,
        merge_footprint_file=local_file,
    )

    assert calls == [None, local_file]
    assert [building.building_id for building in result] == ["local-1"]


def test_explicit_footprint_file_skips_osm(monkeypatch):
    local_file = Path("data/raw/footprints/buildings_index.gpkg")
    calls: list[Path | None] = []

    def fake_query_buildings_in_bbox(
        south: float,
        west: float,
        north: float,
        east: float,
        local_file: Path | None = None,
    ) -> list[BuildingFootprint]:
        calls.append(local_file)
        return [_footprint("local-only-1", "msft")]

    monkeypatch.setattr(
        "stage1_segmentation.pipeline.query_buildings_in_bbox",
        fake_query_buildings_in_bbox,
    )

    result = _query_pipeline_footprints(
        south=-37.92782,
        west=145.10485,
        north=-37.90208,
        east=145.13884,
        footprint_file=local_file,
    )

    assert calls == [local_file]
    assert [building.building_id for building in result] == ["local-only-1"]
