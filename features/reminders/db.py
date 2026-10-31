"""Reminder storage (schema `reminders`). The earliest due time is kept in memory, so the delivery
loop only queries when something is due and Neon can scale to zero while idle."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import asyncpg

from core.db import affected

MAX_PER_USER = 25


def _row(record: asyncpg.Record | None) -> SimpleNamespace | None:
    return SimpleNamespace(**dict(record)) if record is not None else None


class ReminderDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self.next_due: datetime | None = None
        self.loaded = False  # whether next_due has been read from the database since startup

    async def add(self, user_id: int, guild_id: int | None, channel_id: int | None, text: str, private: bool,
                  due_at: datetime) -> SimpleNamespace | None:
        """None when the member already has the most reminders allowed."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('reminders'), hashtext($1::text))", str(user_id))
            if await conn.fetchval("SELECT COUNT(*) FROM reminders.reminders WHERE user_id = $1", user_id) >= MAX_PER_USER:
                return None
            row = _row(await conn.fetchrow(
                "INSERT INTO reminders.reminders (user_id, guild_id, channel_id, text, private, due_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
                user_id, guild_id, channel_id, text, private, due_at,
            ))
        if self.next_due is None or due_at < self.next_due:
            self.next_due = due_at
        return row

    async def set_jump_url(self, reminder_id: int, url: str) -> None:
        await self.pool.execute("UPDATE reminders.reminders SET jump_url = $2 WHERE id = $1", reminder_id, url)

    async def for_user(self, user_id: int) -> list[SimpleNamespace]:
        rows = await self.pool.fetch("SELECT * FROM reminders.reminders WHERE user_id = $1 ORDER BY due_at", user_id)
        return [_row(r) for r in rows]

    async def cancel(self, user_id: int, reminder_id: int) -> bool:
        status = await self.pool.execute(
            "DELETE FROM reminders.reminders WHERE id = $1 AND user_id = $2", reminder_id, user_id
        )
        return affected(status) > 0

    async def refresh_next_due(self) -> None:
        self.next_due = await self.pool.fetchval("SELECT MIN(due_at) FROM reminders.reminders")
        self.loaded = True

    async def take_due(self, limit: int = 50) -> list[SimpleNamespace]:
        """Remove and return reminders that are due. Taking them in one statement means each is
        delivered at most once, even with two instances running."""
        rows = await self.pool.fetch(
            "DELETE FROM reminders.reminders WHERE id IN (SELECT id FROM reminders.reminders WHERE due_at <= NOW() "
            "ORDER BY due_at LIMIT $1 FOR UPDATE SKIP LOCKED) RETURNING *",
            limit,
        )
        return sorted((_row(r) for r in rows), key=lambda r: r.due_at)
