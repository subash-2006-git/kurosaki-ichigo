"""Birthday storage (schema `birthdays`). Enabled servers' settings are cached so the daily check
doesn't query (and wake Neon) every few minutes."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import asyncpg

from core.db import affected

COLUMNS = frozenset({"enabled", "channel_id", "role_id", "message", "timezone", "hour"})
DEFAULTS: dict[str, Any] = {"enabled": False, "channel_id": None, "role_id": None, "message": None,
                            "timezone": "UTC", "hour": 0, "last_run": None}


def _row(record: asyncpg.Record | None) -> SimpleNamespace | None:
    return SimpleNamespace(**dict(record)) if record is not None else None


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


class BirthdayDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._enabled: dict[int, SimpleNamespace] | None = None  # guild_id -> settings, for enabled servers

    # --- settings ---------------------------------------------------------------

    async def settings(self, guild_id: int) -> SimpleNamespace:
        row = await self.pool.fetchrow("SELECT * FROM birthdays.settings WHERE guild_id = $1", guild_id)
        return _row(row) if row else SimpleNamespace(guild_id=guild_id, **DEFAULTS)

    async def update(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - COLUMNS
        if unknown:
            raise ValueError(f"Unknown birthday settings: {', '.join(sorted(unknown))}")
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        row = _row(await self.pool.fetchrow(
            f"INSERT INTO birthdays.settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates} RETURNING *",
            guild_id, *fields.values(),
        ))
        self._enabled = None
        return row

    async def enabled_settings(self) -> list[SimpleNamespace]:
        if self._enabled is None:
            rows = await self.pool.fetch("SELECT * FROM birthdays.settings WHERE enabled")
            self._enabled = {r["guild_id"]: _row(r) for r in rows}
        return list(self._enabled.values())

    async def claim_day(self, guild_id: int, today: date) -> bool:
        """Mark today as handled. False if it already was (another run or instance got there first)."""
        status = await self.pool.execute(
            "UPDATE birthdays.settings SET last_run = $2 WHERE guild_id = $1 AND last_run IS DISTINCT FROM $2",
            guild_id, today,
        )
        if self._enabled and guild_id in self._enabled:
            self._enabled[guild_id].last_run = today
        return affected(status) > 0

    # --- birthdays --------------------------------------------------------------

    async def set(self, guild_id: int, user_id: int, month: int, day: int) -> None:
        await self.pool.execute(
            "INSERT INTO birthdays.birthdays (guild_id, user_id, month, day) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET month = EXCLUDED.month, day = EXCLUDED.day",
            guild_id, user_id, month, day,
        )

    async def remove(self, guild_id: int, user_id: int) -> bool:
        status = await self.pool.execute(
            "DELETE FROM birthdays.birthdays WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )
        return affected(status) > 0

    async def get(self, guild_id: int, user_id: int) -> SimpleNamespace | None:
        return _row(await self.pool.fetchrow(
            "SELECT * FROM birthdays.birthdays WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        ))

    async def on(self, guild_id: int, today: date) -> list[int]:
        """Who has a birthday on this date. 29 February birthdays count on 28 February in other years."""
        leap_day = today.month == 2 and today.day == 28 and not is_leap(today.year)
        rows = await self.pool.fetch(
            "SELECT user_id FROM birthdays.birthdays WHERE guild_id = $1 AND "
            "((month = $2 AND day = $3) OR ($4 AND month = 2 AND day = 29)) ORDER BY user_id",
            guild_id, today.month, today.day, leap_day,
        )
        return [r["user_id"] for r in rows]

    async def all(self, guild_id: int) -> list[SimpleNamespace]:
        rows = await self.pool.fetch("SELECT * FROM birthdays.birthdays WHERE guild_id = $1", guild_id)
        return [_row(r) for r in rows]
