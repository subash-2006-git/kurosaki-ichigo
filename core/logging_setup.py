"""Console + rotating file logging (logs/bot.log)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from core.config import ROOT_DIR

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_FILE = ROOT_DIR / "logs" / "bot.log"


def setup_logging(level: str = "INFO") -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    # Member and server names can contain characters the Windows console can't encode; don't let that raise.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
    root.setLevel(level)
    # Every feature logs at LOG_LEVEL; only the chattiest library loggers are turned down.
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(max(logging.INFO, root.level))
    logging.getLogger("asyncio").setLevel(logging.WARNING)
