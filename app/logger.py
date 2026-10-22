"""Rotating file + console logging."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(logs_dir: Path, level: str = "INFO", max_bytes: int = 5 * 1024 * 1024, backup_count: int = 5) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("docproc")
    if _CONFIGURED:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    logs_dir.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(logs_dir / "docproc.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    _CONFIGURED = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger("docproc" if not name else f"docproc.{name}")