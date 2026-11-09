"""Logging setup: console + rotating file in <data_dir>/logs."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(level: str, data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(getattr(h, "_clipper", False) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        console._clipper = True  # type: ignore[attr-defined]
        fileh = logging.handlers.RotatingFileHandler(
            log_dir / "clipper.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
        )
        fileh.setFormatter(fmt)
        fileh._clipper = True  # type: ignore[attr-defined]
        root.addHandler(console)
        root.addHandler(fileh)
    for noisy in ("httpx", "httpcore", "streamlink", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
