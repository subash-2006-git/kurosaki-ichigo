"""Enums and default texts for the community feature (from general-bot's ORM models, minus the ORM)."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

DEFAULT_WELCOME_TEXT = "Welcome {user} to **{server}**! You're our {membercount_ordinal} member."
DEFAULT_GOODBYE_TEXT = "Goodbye **{username}**. Thanks for being part of {server}."
DEFAULT_GOODBYE_DM_TEXT = "Thanks for being part of {server}. You're welcome back any time!"
DEFAULT_LEVELUP_TEXT = "🎉 {user} reached **Level {level}**!"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RoleMode(str, enum.Enum):
    HIGHEST = "highest"
    KEEP_ALL = "keep_all"


class XpSource(str, enum.Enum):
    MESSAGE = "message"
    VOICE = "voice"
    ADMIN = "admin"
