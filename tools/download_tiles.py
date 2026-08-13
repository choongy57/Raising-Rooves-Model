"""
Download pre-downloaded satellite tiles from Google Drive.

Satellite tiles are ~300 KB each and a full suburb is ~400-700 MB, so they
are NOT stored in git.  They live in the team's Google Drive folder
"Raising Rooves - Shared Data" and this script fetches and extracts them.

Usage:
    python -m tools.download_tiles --suburb clayton
    python -m tools.download_tiles --suburb carlton
    python -m tools.download_tiles --all

The tiles are extracted to data/raw/tiles/{suburb}/ so Stage 1 can run
without a GOOGLE_MAPS_API_KEY (it reuses existing tiles and skips downloads).
"""

import argparse
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import requests

from config.settings import TILES_DIR
from shared.logging_config import setup_logging

logger = setup_logging("download_tiles")

# Google Drive folder: "Raising Rooves - Shared Data"
# File IDs are stable; share the folder with teammates instead of individual files.
TILE_FILE_IDS: dict[str, str] = {
    "carlton": "1fRiu0Rg2zLW3E94berbqjdAu9szmtx8v",
    "clayton": "1StqT0DX8NOcVQtZSQDKS7mcWit_yEcWk",
}


def download_and_extract(suburb_key: str) -> bool:
    """Download a suburb's tile zip from Google Drive and extract it."""
    file_id = TILE_FILE_IDS.get(suburb_key)
    if file_id is None:
        logger.error(
            "No tile zip registered for '%s'. Available: %s",
            suburb_key, sorted(TILE_FILE_IDS),
        )
        return False

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    logger.info("Downloading tiles for %s from Google Drive...", suburb_key)

    # Large files get a virus-scan confirmation page; follow it if needed
    session = requests.Session()
    resp = session.get(url, stream=True, timeout=30)
    if "text/html" in resp.headers.get("Content-Type", ""):
        # Google shows a confirmation page for large files
        for key, value in resp.cookies.items():
            if key.startswith("download_warning"):
                resp = session.get(f"{url}&confirm={value}", stream=True, timeout=30)
                break

    resp.raise_for_status()

    dest_dir = TILES_DIR / suburb_key
    dest_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting to %s...", dest_dir)
    with zipfile.ZipFile(BytesIO(resp.content)) as z:
        z.extractall(TILES_DIR)

    n_tiles = len(list(dest_dir.glob("*.png")))
    logger.info("Done: %d tiles available for %s.", n_tiles, suburb_key)
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Download pre-downloaded satellite tiles from Google Drive"
    )
    parser.add_argument(
        "--suburb",
        type=str,
        help="Suburb to download tiles for (e.g. 'clayton')",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download tiles for all registered suburbs",
    )
    args = parser.parse_args()

    if args.all:
        for suburb_key in TILE_FILE_IDS:
            download_and_extract(suburb_key)
        return

    if not args.suburb:
        parser.error("--suburb is required (or use --all)")

    ok = download_and_extract(args.suburb.lower())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
