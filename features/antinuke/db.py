"""Anti-nuke settings (schema `antinuke`), cached in memory because every audit-log entry reads them."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

from features.antinuke.detector import merged_limits

COLUMNS = frozenset({
    "enabled", "punishment", "limits", "block_bot_add", "revert_dangerous_perms", "restore",
    "trusted_user_ids", "trusted_role_ids", "alert_channel_id",
})
DEFAULTS: dict[str, Any] = {
    "enabled": False, "punishment": "strip_roles", "limits": {}, "block_bot_add": True,
    "revert_dangerous_perms": True, "restore": True, "trusted_user_ids": [], "trusted_role_ids": [],
    "alert_channel_id": None,
}


class AntinukeDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._cache: dict[int, SimpleNamespace] = {}

    async def settings(self, guild_id: int) -> SimpleNamespace:
        cached = self._cache.get(guild_id)
        if cached is None:
            row = await self.pool.fetchrow("SELECT * FROM antinuke.settings WHERE guild_id = $1", guild_id)
            values = dict(row) if row else {"guild_id": guild_id, **DEFAULTS}
            values["limits"] = merged_limits(values["limits"])
            cached = self._cache[guild_id] = SimpleNamespace(**values)
        return cached

    async def update(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - COLUMNS
        if unknown:
            raise ValueError(f"Unknown anti-nuke settings: {', '.join(sorted(unknown))}")
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        await self.pool.execute(
            f"INSERT INTO antinuke.settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}",
            guild_id, *fields.values(),
        )
        self._cache.pop(guild_id, None)
        return await self.settings(guild_id)

    def forget(self, guild_id: int) -> None:
        """Drop the cached settings (after a restore from backup)."""
        self._cache.pop(guild_id, None)
