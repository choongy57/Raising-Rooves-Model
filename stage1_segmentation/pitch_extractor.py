"""
Roof pitch angle extractor for the Raising Rooves pipeline.

Given an (N, 3) array of [X_m, Y_m, Z_m] points sampled from a DSM within
a building footprint, this module:

  1. Removes gross elevation outliers (chimneys, vents, data spikes)
  2. Fits a best-fit plane via RANSAC (robust to multi-planar / partial roofs)
  3. Refines that plane with SVD on all RANSAC inliers
  4. Returns pitch angle (degrees from horizontal) and aspect (degrees from North)

Pitch angle definitions used throughout:
    0°   = perfectly flat roof
    15°  = low-pitch (typical carport / shed)
    22.5° = standard Australian residential pitch
    30°+ = steep / heritage pitch
    > 60° = almost certainly an outlier or wall face

Usage:
    from stage1_segmentation.dsm_processor import load_dsm, extract_building_xyz
    from stage1_segmentation.pitch_extractor import extract_pitch

    dsm = load_dsm(Path("data/raw/dsm/suburb.tif"))
    xyz = extract_building_xyz(dsm, building.polygon_latlon)
    result = extract_pitch(xyz)
    print(result.pitch_deg, result.flag)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from shared.logging_config import setup_logging

logger = setup_logging("pitch_extractor")

# ── Constants ────────────────────────────────────────────────────────────────

_MIN_POINTS = 9           # need at least 9 points to attempt plane fitting
_OUTLIER_MAD_FACTOR = 3.0 # Z-outlier removal: |Z - median| > factor * MAD
_OUTLIER_MIN_DROP_M = 0.5 # don't remove points within 0.5 m of median (avoids over-trimming flat roofs)
_RANSAC_ITERATIONS = 200
_RANSAC_THRESHOLD_M = 0.25  # inlier distance to fitted plane (metres)
_RANSAC_MIN_INLIER_RATIO = 0.35  # reject fit if < 35% of points are inliers
_FLAT_PITCH_DEG = 5.0     # roofs below this are flagged as "flat"
_MAX_REALISTIC_PITCH = 65.0  # above this is flagged "unrealistic"


# ── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class PitchResult:
    """
    Pitch extraction result for a single building.

    Attributes:
        pitch_deg:  Roof pitch in degrees from horizontal (0 = flat).
                    None if extraction failed.
        aspect_deg: Slope direction in degrees clockwise from North (0–360).
                    Points downhill in the direction the roof faces.
                    None if pitch is effectively zero or extraction failed.
        plane_rmse: RMS residual of RANSAC inliers from fitted plane (metres).
                    Low values (< 0.1 m) indicate a clean planar surface.
                    None if extraction failed.
        n_points:   Total DSM points within footprint before outlier removal.
        n_inliers:  Points used for final plane fit (after RANSAC + outlier removal).
        flag:       Quality/diagnostic string:
                    "ok"             — successful fit, pitch looks realistic
                    "flat"           — pitch below 5° (flat or very low-pitch roof)
                    "unrealistic"    — pitch > 65° (likely data artifact or wall face)
                    "too_few_points" — fewer than 9 valid DSM points in footprint
                    "ransac_failed"  — RANSAC could not find a consensus plane
                    "extraction_failed" — exception or empty DSM overlap
    """

    pitch_deg: Optional[float]
    aspect_deg: Optional[float]
    plane_rmse: Optional[float]
    n_points: int
    n_inliers: int
    flag: str


# ── Internal helpers ─────────────────────────────────────────────────────────


def _remove_z_outliers(xyz: np.ndarray) -> np.ndarray:
    """
    Remove elevation spikes (chimneys, vents, data artifacts) using MAD-based
    outlier detection on the Z column.

    Points that are more than _OUTLIER_MAD_FACTOR * MAD above the median are
    removed (upward spikes only — we keep points below median because gutters
    and overhangs are legitimately lower than the ridge).

    Args:
        xyz: (N, 3) float array of [X_m, Y_m, Z_m].

    Returns:
        Filtered (M, 3) array where M <= N.
    """
    if len(xyz) == 0:
        return xyz

    z = xyz[:, 2]
    median_z = np.median(z)
    mad = np.median(np.abs(z - median_z))

    if mad < 1e-6:
        # All points at same elevation — flat roof, keep everything
        return xyz

    upper_bound = median_z + _OUTLIER_MAD_FACTOR * mad
    # Enforce minimum drop so we don't over-trim low-variance flat roofs
    upper_bound = max(upper_bound, median_z + _OUTLIER_MIN_DROP_M)

    return xyz[z <= upper_bound]


def _fit_plane_svd(xyz: np.ndarray) -> tuple[np.ndarray, float]:
    """
    Fit a best-fit plane to point cloud via SVD.

    Returns:
        (normal, rmse) where normal is a unit (3,) vector [a, b, c] in
        the equation a*x + b*y + c*z = d, and rmse is the RMS point-to-plane
        distance in metres.

    The normal is oriented so that c (vertical component) is positive
    (i.e. points upward from the roof surface).
    """
    centroid = xyz.mean(axis=0)
    centered = xyz - centroid
    _, _, Vt = np.linalg.svd(centered, full_matrices=False)
    normal = Vt[-1]  # eigenvector corresponding to smallest singular value

    # Ensure normal points upward
    if normal[2] < 0:
        normal = -normal

    # RMSE
    distances = centered @ normal  # dot product = distance to plane through origin
    rmse = float(np.sqrt(np.mean(distances ** 2)))

    return normal, rmse


def _ransac_plane(
    xyz: np.ndarray,
    n_iterations: int = _RANSAC_ITERATIONS,
    threshold_m: float = _RANSAC_THRESHOLD_M,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    RANSAC robust plane fitting.

    Iteratively samples 3 random points, fits a plane, and counts inliers.
    Returns the inlier mask for the best iteration.

    Args:
        xyz:          (N, 3) point array.
        n_iterations: Number of RANSAC trials.
        threshold_m:  Max point-to-plane distance to count as an inlier (metres).
        rng:          Optional numpy random generator (for reproducibility).

    Returns:
        (best_normal, inlier_mask) — normal is a unit (3,) vector, inlier_mask
        is a boolean (N,) array marking inlier points.

    Raises:
        ValueError: If xyz has fewer than 3 points.
    """
    if rng is None:
        rng = np.random.default_rng(42)

    n = len(xyz)
    if n < 3:
        raise ValueError(f"Need at least 3 points for RANSAC, got {n}.")

    best_normal = np.array([0.0, 0.0, 1.0])
    best_mask = np.zeros(n, dtype=bool)
    best_inlier_count = 0

    for _ in range(n_iterations):
        # Sample 3 distinct points
        idxs = rng.choice(n, size=3, replace=False)
        p1, p2, p3 = xyz[idxs]

        # Plane normal via cross product
        v1 = p2 - p1
        v2 = p3 - p1
        normal = np.cross(v1, v2)
        norm_len = np.linalg.norm(normal)
        if norm_len < 1e-9:
            continue  # degenerate (collinear points)
        normal = normal / norm_len
        if normal[2] < 0:
            normal = -normal

        # Signed distance of each point to this plane
        d = np.dot(p1, normal)
        distances = np.abs(xyz @ normal - d)
        mask = distances < threshold_m
        count = mask.sum()

        if count > best_inlier_count:
            best_inlier_count = count
            best_mask = mask
            best_normal = normal

    return best_normal, best_mask


def _normal_to_pitch_aspect(normal: np.ndarray) -> tuple[float, float]:
    """
    Convert a unit plane normal vector to (pitch_deg, aspect_deg).

    Args:
        normal: Unit vector [a, b, c] where c is the vertical component.

    Returns:
        (pitch_deg, aspect_deg):
            pitch_deg  — angle from horizontal in degrees [0, 90]
            aspect_deg — downhill direction in degrees clockwise from North [0, 360)
    """
    # Ensure normalised
    n = normal / np.linalg.norm(normal)
    nx, ny, nz = n

    # Pitch: angle between normal and vertical axis
    # sin(pitch) = horizontal component magnitude = sqrt(nx² + ny²)
    horizontal = math.sqrt(nx ** 2 + ny ** 2)
    pitch_deg = math.degrees(math.atan2(horizontal, abs(nz)))

    # Aspect: direction the slope faces (downhill direction of the roof surface)
    # In our coordinate system: X = East, Y = North
    # The slope direction (downhill) is the projection of the normal onto the
    # horizontal plane, pointing away from the high side.
    # atan2(nx, ny) gives angle from North (Y axis) clockwise toward East (X axis).
    if horizontal < 1e-6:
        aspect_deg = 0.0  # flat — aspect undefined, default to North
    else:
        aspect_deg = math.degrees(math.atan2(nx, ny)) % 360.0

    return pitch_deg, aspect_deg


# ── Public API ────────────────────────────────────────────────────────────────


def extract_pitch(
    xyz: np.ndarray,
    ransac_iterations: int = _RANSAC_ITERATIONS,
    ransac_threshold_m: float = _RANSAC_THRESHOLD_M,
    rng: np.random.Generator | None = None,
) -> PitchResult:
    """
    Extract roof pitch angle from a set of DSM points within a building footprint.

    Pipeline:
        1. Remove upward Z-outliers (chimneys, vents, spikes)
        2. RANSAC to find the dominant roof plane
        3. SVD refit on RANSAC inliers for accurate normal vector
        4. Convert normal to pitch + aspect

    Args:
        xyz:                (N, 3) float array [X_m, Y_m, Z_m] from extract_building_xyz().
        ransac_iterations:  Number of RANSAC trials (default 200).
        ransac_threshold_m: Inlier distance threshold in metres (default 0.25 m).
        rng:                Optional numpy random generator.

    Returns:
        PitchResult with pitch_deg, aspect_deg, plane_rmse, n_points, n_inliers, flag.
    """
    n_raw = len(xyz)

    # ── Guard: empty input ────────────────────────────────────────────────
    if n_raw == 0:
        return PitchResult(
            pitch_deg=None, aspect_deg=None, plane_rmse=None,
            n_points=0, n_inliers=0, flag="extraction_failed",
        )

    # ── Step 1: Remove Z outliers ─────────────────────────────────────────
    xyz_clean = _remove_z_outliers(xyz)

    if len(xyz_clean) < _MIN_POINTS:
        return PitchResult(
            pitch_deg=None, aspect_deg=None, plane_rmse=None,
            n_points=n_raw, n_inliers=len(xyz_clean), flag="too_few_points",
        )

    # ── Step 2: RANSAC ────────────────────────────────────────────────────
    try:
        _, inlier_mask = _ransac_plane(
            xyz_clean,
            n_iterations=ransac_iterations,
            threshold_m=ransac_threshold_m,
            rng=rng,
        )
    except Exception as exc:
        logger.debug("RANSAC failed: %s", exc)
        return PitchResult(
            pitch_deg=None, aspect_deg=None, plane_rmse=None,
            n_points=n_raw, n_inliers=0, flag="ransac_failed",
        )

    n_inliers = int(inlier_mask.sum())
    inlier_ratio = n_inliers / len(xyz_clean)

    if inlier_ratio < _RANSAC_MIN_INLIER_RATIO or n_inliers < _MIN_POINTS:
        return PitchResult(
            pitch_deg=None, aspect_deg=None, plane_rmse=None,
            n_points=n_raw, n_inliers=n_inliers, flag="ransac_failed",
        )

    # ── Step 3: SVD refit on inliers ──────────────────────────────────────
    inliers = xyz_clean[inlier_mask]
    try:
        normal, rmse = _fit_plane_svd(inliers)
    except Exception as exc:
        logger.debug("SVD refit failed: %s", exc)
        return PitchResult(
            pitch_deg=None, aspect_deg=None, plane_rmse=None,
            n_points=n_raw, n_inliers=n_inliers, flag="ransac_failed",
        )

    # ── Step 4: Convert to pitch + aspect ─────────────────────────────────
    pitch_deg, aspect_deg = _normal_to_pitch_aspect(normal)
    pitch_deg = round(pitch_deg, 1)
    aspect_deg = round(aspect_deg, 1)
    rmse = round(rmse, 3)

    # ── Flag assignment ───────────────────────────────────────────────────
    if pitch_deg > _MAX_REALISTIC_PITCH:
        flag = "unrealistic"
    elif pitch_deg < _FLAT_PITCH_DEG:
        flag = "flat"
    else:
        flag = "ok"

    return PitchResult(
        pitch_deg=pitch_deg,
        aspect_deg=aspect_deg,
        plane_rmse=rmse,
        n_points=n_raw,
        n_inliers=n_inliers,
        flag=flag,
    )


def batch_extract_pitch(
    dsm: "DSMRaster",  # noqa: F821 — imported by caller
    polygon_latlons: list[list[list[float]]],
    building_ids: list[str],
    buffer_m: float = 0.5,
) -> list[PitchResult]:
    """
    Extract pitch for a list of building polygons against a single DSM.

    Args:
        dsm:             Open DSMRaster from load_dsm().
        polygon_latlons: List of polygons, each as [[lon, lat], ...].
        building_ids:    Corresponding building IDs (for logging only).
        buffer_m:        Outward buffer applied when clipping DSM (metres).

    Returns:
        List of PitchResult, one per polygon, in the same order.
    """
    from stage1_segmentation.dsm_processor import extract_building_xyz

    results: list[PitchResult] = []
    rng = np.random.default_rng(42)

    failed = 0
    for bid, poly in zip(building_ids, polygon_latlons):
        try:
            xyz = extract_building_xyz(dsm, poly, buffer_m=buffer_m)
            result = extract_pitch(xyz, rng=rng)
        except Exception as exc:
            logger.debug("Pitch extraction error for building %s: %s", bid, exc)
            result = PitchResult(
                pitch_deg=None, aspect_deg=None, plane_rmse=None,
                n_points=0, n_inliers=0, flag="extraction_failed",
            )
            failed += 1
        results.append(result)

    total = len(results)
    ok = sum(1 for r in results if r.flag in {"ok", "flat"})
    logger.info(
        "Pitch extraction: %d/%d succeeded (%d failed/unrealistic)",
        ok, total, total - ok,
    )
    return results
