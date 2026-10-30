"""Every environment variable the bot reads, in one place.

Values come from the process environment, with `.env` in the project root as a local fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int | None = None) -> int | None:
    value = _str(name)
    return int(value) if value else default


def _float(name: str, default: float) -> float:
    value = _str(name)
    return float(value) if value else default


def _bool(name: str, default: bool = False) -> bool:
    value = _str(name).lower()
    return value in {"1", "true", "yes", "on"} if value else default


# ---- core -----------------------------------------------------------------
DISCORD_TOKEN = _str("DISCORD_TOKEN")
DATABASE_URL = _str("DATABASE_URL")
DATABASE_SSL = _str("DATABASE_SSL", "require")  # "disable" for a local Postgres without TLS
PORT = _int("PORT", 8080)
LOG_LEVEL = _str("LOG_LEVEL", "INFO").upper()
DEV_GUILD_ID = _int("DEV_GUILD_ID")
SYNC_COMMANDS = _bool("SYNC_COMMANDS")
BOT_NAME = _str("BOT_NAME")
CONTACT_EMAIL = _str("CONTACT_EMAIL")

# ---- logging feature ------------------------------------------------------
STATS_UPDATE_INTERVAL = _int("STATS_UPDATE_INTERVAL", 30)
STATS_RECONCILE_INTERVAL = _int("STATS_RECONCILE_INTERVAL", 600)

# ---- nickname feature -----------------------------------------------------
RENAME_DELAY_SECONDS = _float("RENAME_DELAY_SECONDS", 1.0)

# ---- music feature --------------------------------------------------------
DEFAULT_VOLUME = _int("DEFAULT_VOLUME", 50)
IDLE_TIMEOUT = _int("IDLE_TIMEOUT", 300)
ALONE_TIMEOUT = _int("ALONE_TIMEOUT", 60)
MAX_QUEUE = _int("MAX_QUEUE", 200)
YTDL_COOKIES_FILE = _str("YTDL_COOKIES_FILE")
AUTO_UPDATE = _bool("AUTO_UPDATE")


def missing_required() -> list[str]:
    return [name for name, value in (("DISCORD_TOKEN", DISCORD_TOKEN), ("DATABASE_URL", DATABASE_URL)) if not value]
