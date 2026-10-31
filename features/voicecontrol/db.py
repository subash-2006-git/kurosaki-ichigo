"""Voice command settings (schema `voicecontrol`): who the commands skip. Cached per server."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

COLUMNS = frozenset({"exempt_role_ids", "exempt_user_ids"})


class VoiceControlDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._cache: dict[int, SimpleNamespace] = {}

    async def settings(self, guild_id: int) -> SimpleNamespace:
        cached = self._cache.get(guild_id)
        if cached is None:
            row = await self.pool.fetchrow("SELECT * FROM voicecontrol.settings WHERE guild_id = $1", guild_id)
            cached = self._cache[guild_id] = SimpleNamespace(**dict(row)) if row else SimpleNamespace(
                guild_id=guild_id, exempt_role_ids=[], exempt_user_ids=[])
        return cached

    def forget(self, guild_id: int) -> None:
        """Drop the cached settings (after a restore from backup)."""
        self._cache.pop(guild_id, None)

    async def update(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - COLUMNS
        if unknown:
            raise ValueError(f"Unknown voice command settings: {', '.join(sorted(unknown))}")
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        await self.pool.execute(
            f"INSERT INTO voicecontrol.settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}",
            guild_id, *fields.values(),
        )
        self._cache.pop(guild_id, None)
        return await self.settings(guild_id)
