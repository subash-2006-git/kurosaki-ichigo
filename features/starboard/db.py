"""Starboard storage (schema `starboard`). Settings are cached: every matching reaction reads them."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

COLUMNS = frozenset({"enabled", "channel_id", "emoji", "threshold", "allow_self"})
DEFAULTS: dict[str, Any] = {"enabled": False, "channel_id": None, "emoji": "⭐", "threshold": 3, "allow_self": False}


class StarboardDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._cache: dict[int, SimpleNamespace] = {}

    async def settings(self, guild_id: int) -> SimpleNamespace:
        cached = self._cache.get(guild_id)
        if cached is None:
            row = await self.pool.fetchrow("SELECT * FROM starboard.settings WHERE guild_id = $1", guild_id)
            cached = self._cache[guild_id] = SimpleNamespace(**(dict(row) if row else {"guild_id": guild_id, **DEFAULTS}))
        return cached

    async def update(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - COLUMNS
        if unknown:
            raise ValueError(f"Unknown starboard settings: {', '.join(sorted(unknown))}")
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        await self.pool.execute(
            f"INSERT INTO starboard.settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}",
            guild_id, *fields.values(),
        )
        self._cache.pop(guild_id, None)
        return await self.settings(guild_id)

    async def entry(self, message_id: int) -> SimpleNamespace | None:
        row = await self.pool.fetchrow("SELECT * FROM starboard.entries WHERE message_id = $1", message_id)
        return SimpleNamespace(**dict(row)) if row else None

    async def save_entry(self, message_id: int, guild_id: int, channel_id: int, author_id: int,
                         star_message_id: int, stars: int) -> None:
        await self.pool.execute(
            "INSERT INTO starboard.entries (message_id, guild_id, channel_id, author_id, star_message_id, stars) "
            "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (message_id) DO UPDATE SET "
            "star_message_id = EXCLUDED.star_message_id, stars = EXCLUDED.stars",
            message_id, guild_id, channel_id, author_id, star_message_id, stars,
        )

    async def delete_entry(self, message_id: int) -> None:
        await self.pool.execute("DELETE FROM starboard.entries WHERE message_id = $1", message_id)

    async def entries_by_author(self, author_id: int) -> list[SimpleNamespace]:
        rows = await self.pool.fetch("SELECT * FROM starboard.entries WHERE author_id = $1", author_id)
        return [SimpleNamespace(**dict(r)) for r in rows]
