import copy
import re
from dataclasses import dataclass, field
from datetime import datetime

DEFAULT_AUTOMOD = {
    "enabled": True,
    "badwords": True,
    "invites": True,
    "links": False,
    "spam": {"enabled": True, "messages": 5, "seconds": 5},
    "duplicates": {"enabled": True, "count": 3, "seconds": 30},
    "mentions": {"enabled": True, "max": 5},
    "exempt_roles": [],
    "exempt_channels": [],
}

DEFAULT_RAID = {
    "enabled": True,
    "joins": 10,
    "seconds": 10,
    "min_account_age_days": 0,
    "young_account_action": "kick",  # or "alert": let them in and tell the mods
    "auto_end_minutes": 0,           # 0: raid mode stays on until someone turns it off
}


def _merge(defaults: dict, stored: dict | None) -> dict:
    """Overlay stored settings onto defaults so new keys always have a value."""
    result = copy.deepcopy(defaults)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class GuildSettings:
    guild_id: int
    modlog_channel_id: int | None = None
    report_channel_id: int | None = None
    alert_channel_id: int | None = None
    appeal_url: str | None = None
    dm_on_action: bool = True
    warn_expiry_days: int = 0
    raidmode_active: bool = False
    raidmode_since: datetime | None = None
    locked_channels: list[int] = field(default_factory=list)
    automod: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_AUTOMOD))
    raid: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_RAID))
    banned_words: list[str] = field(default_factory=list)
    whitelist: set[str] = field(default_factory=set)
    word_pattern: re.Pattern | None = None

    @property
    def alert_channel(self) -> int | None:
        """Raid alerts go to the alert channel, falling back to the mod-log."""
        return self.alert_channel_id or self.modlog_channel_id


class GuildCache:
    """Caches per-guild settings so automod doesn't hit the database on every message."""

    def __init__(self, db):
        self.db = db
        self._cache: dict[int, GuildSettings] = {}

    async def get(self, guild_id: int) -> GuildSettings:
        settings = self._cache.get(guild_id)
        if settings is None:
            settings = await self._load(guild_id)
            self._cache[guild_id] = settings
        return settings

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    async def update(self, guild_id: int, **fields) -> None:
        await self.db.update_guild_config(guild_id, **fields)
        self.invalidate(guild_id)

    async def _load(self, guild_id: int) -> GuildSettings:
        row = await self.db.get_guild_config(guild_id)
        words = await self.db.get_banned_words(guild_id)
        whitelist = await self.db.get_whitelist(guild_id)

        settings = GuildSettings(guild_id=guild_id)
        if row is not None:
            settings.modlog_channel_id = row["modlog_channel_id"]
            settings.report_channel_id = row["report_channel_id"]
            settings.alert_channel_id = row["alert_channel_id"]
            settings.appeal_url = row["appeal_url"]
            settings.dm_on_action = row["dm_on_action"]
            settings.warn_expiry_days = row["warn_expiry_days"]
            settings.raidmode_active = row["raidmode_active"]
            settings.raidmode_since = row["raidmode_since"]
            settings.locked_channels = list(row["locked_channels"] or [])
            settings.automod = _merge(DEFAULT_AUTOMOD, row["automod"])
            settings.raid = _merge(DEFAULT_RAID, row["raid"])

        settings.banned_words = words
        settings.whitelist = set(whitelist)
        if words:
            alternatives = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
            settings.word_pattern = re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.IGNORECASE)
        return settings
