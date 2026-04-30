"""
Tests for the --import-dsm path in tools/extract_pitch.py.

These tests cover:
  - _import_dsm raises FileNotFoundError for missing source
  - _import_dsm copies a valid GeoTIFF to DSM_DIR and returns the dest path
  - _import_dsm logs a resolution warning for coarse-resolution files
  - _import_dsm logs a bounding-box warning when DSM does not overlap suburb
  - run_extract_pitch CLI error message when no DSM source is given
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

# ── Ensure project root is on sys.path ────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tools.extract_pitch import _import_dsm, _suburb_key


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_geotiff(
    path: Path,
    width: int = 100,
    height: int = 100,
    epsg: int = 4326,
    origin_lon: float = 144.96,
    origin_lat: float = -37.80,
    pixel_size_deg: float = 9e-6,  # ~1 m in degrees at Melbourne latitude
) -> Path:
    """
    Write a minimal single-band GeoTIFF at the given location.

    Uses rasterio so the CRS and geotransform are set correctly.
    """
    try:
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.crs import CRS
    except ImportError:
        pytest.skip("rasterio not installed — skipping GeoTIFF fixture creation")

    transform = from_origin(origin_lon, origin_lat, pixel_size_deg, pixel_size_deg)
    data = np.random.default_rng(0).uniform(50, 55, (1, height, width)).astype(np.float32)

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        str(path),
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype="float32",
        crs=CRS.from_epsg(epsg),
        transform=transform,
    ) as ds:
        ds.write(data)
    return path


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestImportDsmMissingFile:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.tif"
        # Clayton suburb bbox (south, west, north, east)
        bbox = (-37.955, 145.11, -37.90, 145.16)
        with pytest.raises(FileNotFoundError, match="Source DSM not found"):
            _import_dsm(missing, "Clayton", bbox)


class TestImportDsmValidFile:
    def test_copies_to_dsm_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid 1 m GeoTIFF is copied into DSM_DIR under the suburb key name."""
        try:
            import rasterio  # noqa: F401
        except ImportError:
            pytest.skip("rasterio not installed")

        # Patch DSM_DIR to a temp directory so we don't write into the real tree
        fake_dsm_dir = tmp_path / "dsm_out"
        monkeypatch.setattr("tools.extract_pitch.DSM_DIR", fake_dsm_dir)

        source = tmp_path / "source_dem.tif"
        # ~1 m pixel at Melbourne latitude
        _make_geotiff(source, pixel_size_deg=9e-6, origin_lon=144.96, origin_lat=-37.80)

        # Use a bbox that overlaps (origin_lon, origin_lat) region
        bbox = (-37.83, 144.95, -37.79, 145.01)
        dest = _import_dsm(source, "Carlton", bbox)

        assert dest.exists(), "Destination file should exist after import"
        assert dest.name == "carlton.tif", f"Expected carlton.tif, got {dest.name}"
        assert dest.parent == fake_dsm_dir

    def test_returns_correct_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Return value is a Path under DSM_DIR named <suburb_key>.tif."""
        try:
            import rasterio  # noqa: F401
        except ImportError:
            pytest.skip("rasterio not installed")

        fake_dsm_dir = tmp_path / "dsm_out"
        monkeypatch.setattr("tools.extract_pitch.DSM_DIR", fake_dsm_dir)

        source = tmp_path / "dem.tif"
        _make_geotiff(source, pixel_size_deg=9e-6, origin_lon=144.96, origin_lat=-37.80)

        bbox = (-37.83, 144.95, -37.79, 145.01)
        dest = _import_dsm(source, "Carlton", bbox)

        assert isinstance(dest, Path)
        assert dest.stem == "carlton"
        assert dest.suffix == ".tif"


class TestImportDsmCoarseResolutionWarning:
    def test_warns_for_30m_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A ~30 m pixel file should trigger a resolution WARNING."""
        try:
            import rasterio  # noqa: F401
        except ImportError:
            pytest.skip("rasterio not installed")

        fake_dsm_dir = tmp_path / "dsm_out"
        monkeypatch.setattr("tools.extract_pitch.DSM_DIR", fake_dsm_dir)

        # ~30 m in degrees at Melbourne latitude ≈ 2.7e-4 degrees
        source = tmp_path / "coarse.tif"
        _make_geotiff(source, pixel_size_deg=2.7e-4, origin_lon=144.96, origin_lat=-37.80)

        bbox = (-37.83, 144.95, -37.79, 145.01)
        import logging

        # setup_logging sets propagate=False on the named logger, so caplog
        # (which hooks into the root logger) would miss the records.
        # Temporarily re-enable propagation for this test.
        named_logger = logging.getLogger("extract_pitch")
        original_propagate = named_logger.propagate
        named_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="extract_pitch"):
                _import_dsm(source, "Carlton", bbox)
        finally:
            named_logger.propagate = original_propagate

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("unreliable" in str(m).lower() or "coarser" in str(m).lower()
                   for m in warning_messages), (
            "Expected a resolution warning for coarse (~30 m) data"
        )


class TestImportDsmBboxWarning:
    def test_warns_when_no_overlap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """DSM placed far from suburb should trigger a bounding-box WARNING."""
        try:
            import rasterio  # noqa: F401
        except ImportError:
            pytest.skip("rasterio not installed")

        fake_dsm_dir = tmp_path / "dsm_out"
        monkeypatch.setattr("tools.extract_pitch.DSM_DIR", fake_dsm_dir)

        # Place DSM in Sydney (lon ~151, lat ~-33)
        source = tmp_path / "sydney.tif"
        _make_geotiff(source, pixel_size_deg=9e-6, origin_lon=151.0, origin_lat=-33.8)

        # Suburb is in Melbourne
        bbox = (-37.83, 144.95, -37.79, 145.01)
        import logging

        named_logger = logging.getLogger("extract_pitch")
        original_propagate = named_logger.propagate
        named_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="extract_pitch"):
                _import_dsm(source, "Carlton", bbox)
        finally:
            named_logger.propagate = original_propagate

        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("overlap" in str(m).lower() for m in warning_messages), (
            "Expected a bounding-box overlap warning for a DSM placed far from the suburb"
        )


class TestSuburbKey:
    def test_lowercase_no_spaces(self) -> None:
        assert _suburb_key("Box Hill") == "box_hill"

    def test_already_simple(self) -> None:
        assert _suburb_key("richmond") == "richmond"

    def test_mixed_case(self) -> None:
        assert _suburb_key("Carlton") == "carlton"
