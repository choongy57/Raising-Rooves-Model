"""
Structured logging setup for the Raising Rooves pipeline.

Logs to both console (with colour) and file (plain text).
Toggle debug mode with --debug CLI flag or by calling setup_logging(level="DEBUG").
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from config.settings import LOGS_DIR


def setup_logging(module_name: str, level: str = "INFO") -> logging.Logger:
    """
    Configure and return a logger for the given module.

    Args:
        module_name: Name of the module (used in log file name and log prefix).
        level: Logging level — "DEBUG", "INFO", "WARNING", "ERROR".

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(module_name)

    # Avoid adding duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-8s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    log_file = LOGS_DIR / f"{module_name}_{date_str}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger
