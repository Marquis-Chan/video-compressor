"""Best-effort local diagnostics for the compression runtime."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import Any

from .platform_support import default_log_dir


_LOGGER_NAME = "video_compressor.runtime"


_default_log_dir = default_log_dir


def get_logger() -> logging.Logger:
    """Return a local rotating logger without letting setup failures escape."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        directory = _default_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            directory / "video-compressor.log",
            maxBytes=1_048_576,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except Exception:
        logger.addHandler(logging.NullHandler())
    return logger


def record_event(level: int, phase: str, **fields: Any) -> None:
    """Record metadata only; callers never need diagnostics to succeed."""
    try:
        detail = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
        get_logger().log(level, "phase=%s %s", phase, detail)
    except Exception:
        pass
