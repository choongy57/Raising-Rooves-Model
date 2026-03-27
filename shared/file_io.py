"""
File I/O utilities for the Raising Rooves pipeline.

Handles CSV and Parquet read/write with schema validation.
All data persistence goes through these functions.
"""

from pathlib import Path

import pandas as pd


def ensure_dir(path: Path) -> Path:
    """Create a directory (and parents) if it doesn't exist. Returns the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_parquet(df: pd.DataFrame, path: Path, required_columns: list[str] | None = None) -> Path:
    """
    Save a DataFrame to Parquet with optional column validation.

    Args:
        df: DataFrame to save.
        path: Output file path.
        required_columns: If provided, validates these columns exist before saving.

    Returns:
        The path the file was saved to.

    Raises:
        ValueError: If required columns are missing.
    """
    if required_columns:
        missing = set(required_columns) - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")

    ensure_dir(path.parent)
    df.to_parquet(path, index=False, engine="pyarrow")
    return path


def load_parquet(path: Path, required_columns: list[str] | None = None) -> pd.DataFrame:
    """
    Load a Parquet file with optional column validation.

    Args:
        path: Path to the Parquet file.
        required_columns: If provided, validates these columns exist after loading.

    Returns:
        Loaded DataFrame.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If required columns are missing.
    """
    if not path.exists():
        raise FileNotFoundError(f"Parquet file not found: {path}")

    df = pd.read_parquet(path, engine="pyarrow")

    if required_columns:
        missing = set(required_columns) - set(df.columns)
        if missing:
            raise ValueError(f"Parquet file missing required columns: {missing}")

    return df


def save_csv(df: pd.DataFrame, path: Path) -> Path:
    """Save a DataFrame to CSV. Returns the path."""
    ensure_dir(path.parent)
    df.to_csv(path, index=False)
    return path


def load_csv(path: Path) -> pd.DataFrame:
    """Load a CSV file into a DataFrame."""
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    return pd.read_csv(path)
