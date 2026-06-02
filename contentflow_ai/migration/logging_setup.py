from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class StripAnsiFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _ANSI.sub("", super().format(record))


def setup_logging(name: str, *, log_dir: str | Path = "logs") -> tuple[logging.Logger, Path, Path]:
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    full_path = directory / f"{name}_{timestamp}_full.log"
    error_path = directory / f"{name}_{timestamp}_errors.log"

    logger = logging.getLogger("contentflow_ai")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = StripAnsiFormatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    full_handler = logging.FileHandler(full_path, encoding="utf-8")
    full_handler.setLevel(logging.DEBUG)
    full_handler.setFormatter(fmt)
    logger.addHandler(full_handler)

    error_handler = logging.FileHandler(error_path, encoding="utf-8")
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(fmt)
    logger.addHandler(error_handler)
    return logger, full_path, error_path
