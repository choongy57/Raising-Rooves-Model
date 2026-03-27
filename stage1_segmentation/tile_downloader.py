"""
Satellite tile downloader for the Raising Rooves pipeline.

Downloads high-resolution satellite imagery tiles from Google Maps Static API
for a given suburb bounding box. Tiles are cached locally — already-downloaded
tiles are skipped (idempotent).
"""

import time
from pathlib import Path

import requests

from config.settings import (
    DEFAULT_MAP_TYPE,
    DEFAULT_TILE_SIZE,
    DEFAULT_ZOOM,
    GOOGLE_MAPS_API_KEY,
    GOOGLE_MAPS_BASE_URL,
    MAX_RETRIES,
    RETRY_BACKOFF,
    TILE_DOWNLOAD_DELAY,
    TILES_DIR,
)
from shared.file_io import ensure_dir
from shared.geo_utils import compute_tile_grid, tile_centre_latlon
from shared.logging_config import setup_logging
from shared.validation import validate_bbox, validate_env_vars

logger = setup_logging("tile_downloader")


def _build_tile_url(lat: float, lon: float, zoom: int, size: int) -> str:
    """Build a Google Maps Static API URL for a satellite tile."""
    return (
        f"{GOOGLE_MAPS_BASE_URL}"
        f"?center={lat},{lon}"
        f"&zoom={zoom}"
        f"&size={size}x{size}"
        f"&maptype={DEFAULT_MAP_TYPE}"
        f"&key={GOOGLE_MAPS_API_KEY}"
    )


def _download_single_tile(url: str, output_path: Path) -> bool:
    """
    Download a single tile with retry logic.

    Returns True if the tile was downloaded successfully, False otherwise.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()

            # Check content type is actually an image
            content_type = response.headers.get("Content-Type", "")
            if "image" not in content_type:
                logger.warning(
                    "Non-image response (Content-Type: %s) for %s",
                    content_type,
                    output_path.name,
                )
                return False

            output_path.write_bytes(response.content)
            return True

        except requests.RequestException as e:
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF ** attempt
                logger.warning(
                    "Attempt %d/%d failed for %s: %s. Retrying in %.1fs...",
                    attempt,
                    MAX_RETRIES,
                    output_path.name,
                    e,
                    wait,
                )
                time.sleep(wait)
            else:
                logger.error(
                    "All %d attempts failed for %s: %s",
                    MAX_RETRIES,
                    output_path.name,
                    e,
                )
                return False


def download_tiles(
    suburb_name: str,
    bbox: tuple[float, float, float, float],
    zoom: int = DEFAULT_ZOOM,
) -> list[Path]:
    """
    Download satellite tiles covering a suburb's bounding box.

    Tiles that already exist on disk are skipped. Each tile is named
    {suburb}_{zoom}_{x}_{y}.png.

    Args:
        suburb_name: Name of the suburb (used in file naming).
        bbox: (south, west, north, east) bounding box in EPSG:4326.
        zoom: Map zoom level (default 19, ~0.29 m/pixel at Melbourne).

    Returns:
        List of Paths to all tile files (including previously downloaded ones).
    """
    # Validate inputs
    validate_bbox(bbox)
    validate_env_vars(["GOOGLE_MAPS_API_KEY"])

    # Set up output directory
    suburb_key = suburb_name.lower().replace(" ", "_")
    output_dir = ensure_dir(TILES_DIR / suburb_key)

    # Compute which tiles we need
    tile_coords = compute_tile_grid(bbox, zoom)
    logger.info(
        "Suburb '%s': %d tiles to cover bbox at zoom %d",
        suburb_name,
        len(tile_coords),
        zoom,
    )

    downloaded_paths = []
    skipped = 0
    failed = 0

    for i, (x, y) in enumerate(tile_coords):
        filename = f"{suburb_key}_{zoom}_{x}_{y}.png"
        tile_path = output_dir / filename

        # Skip if already downloaded
        if tile_path.exists() and tile_path.stat().st_size > 0:
            downloaded_paths.append(tile_path)
            skipped += 1
            continue

        # Get tile centre coordinates for the API request
        lat, lon = tile_centre_latlon(x, y, zoom)
        url = _build_tile_url(lat, lon, zoom, DEFAULT_TILE_SIZE)

        success = _download_single_tile(url, tile_path)
        if success:
            downloaded_paths.append(tile_path)
            logger.debug("Downloaded tile %d/%d: %s", i + 1, len(tile_coords), filename)
        else:
            failed += 1
            logger.warning("Failed to download tile: %s", filename)

        # Rate limiting
        time.sleep(TILE_DOWNLOAD_DELAY)

    logger.info(
        "Download complete for '%s': %d tiles (%d new, %d skipped, %d failed)",
        suburb_name,
        len(downloaded_paths),
        len(downloaded_paths) - skipped,
        skipped,
        failed,
    )

    return downloaded_paths
