"""Suggestions storage (schema `suggestions`) on the bot's shared pool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

from core.db import affected

COLUMNS = frozenset({"enabled", "channel_id", "threads"})
DEFAULTS: dict[str, Any] = {"enabled": False, "channel_id": None, "threads": False, "counter": 0}


def _row(record: asyncpg.Record | None) -> SimpleNamespace | None:
    return SimpleNamespace(**dict(record)) if record is not None else None


class SuggestionsDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    # --- settings ---------------------------------------------------------------

    async def settings(self, guild_id: int) -> SimpleNamespace:
        row = await self.pool.fetchrow("SELECT * FROM suggestions.settings WHERE guild_id = $1", guild_id)
        return _row(row) if row else SimpleNamespace(guild_id=guild_id, **DEFAULTS)

    async def update(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - COLUMNS
        if unknown:
            raise ValueError(f"Unknown suggestion settings: {', '.join(sorted(unknown))}")
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        return _row(await self.pool.fetchrow(
            f"INSERT INTO suggestions.settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates} RETURNING *",
            guild_id, *fields.values(),
        ))

    # --- suggestions ------------------------------------------------------------

    async def create(self, guild_id: int, author_id: int, channel_id: int, content: str) -> SimpleNamespace:
        async with self.pool.acquire() as conn, conn.transaction():
            number = await conn.fetchval(
                "INSERT INTO suggestions.settings (guild_id, counter) VALUES ($1, 1) "
                "ON CONFLICT (guild_id) DO UPDATE SET counter = suggestions.settings.counter + 1 RETURNING counter",
                guild_id,
            )
            return _row(await conn.fetchrow(
                "INSERT INTO suggestions.suggestions (guild_id, number, author_id, channel_id, content) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                guild_id, number, author_id, channel_id, content,
            ))

    async def set_message(self, suggestion_id: int, message_id: int) -> None:
        await self.pool.execute("UPDATE suggestions.suggestions SET message_id = $2 WHERE id = $1", suggestion_id, message_id)

    async def get(self, suggestion_id: int) -> SimpleNamespace | None:
        return _row(await self.pool.fetchrow("SELECT * FROM suggestions.suggestions WHERE id = $1", suggestion_id))

    async def delete(self, suggestion_id: int) -> None:
        await self.pool.execute("DELETE FROM suggestions.suggestions WHERE id = $1", suggestion_id)

    async def decide(self, suggestion_id: int, status: str, staff_id: int, reason: str | None) -> SimpleNamespace | None:
        """Accept or deny an open suggestion. None if it was already decided."""
        return _row(await self.pool.fetchrow(
            "UPDATE suggestions.suggestions SET status = $2, decided_by = $3, decision_reason = $4, decided_at = NOW() "
            "WHERE id = $1 AND status = 'open' RETURNING *",
            suggestion_id, status, staff_id, reason,
        ))

    async def counts(self, guild_id: int) -> dict[str, int]:
        rows = await self.pool.fetch(
            "SELECT status, COUNT(*) AS n FROM suggestions.suggestions WHERE guild_id = $1 GROUP BY status", guild_id
        )
        return {"open": 0, "accepted": 0, "denied": 0, **{r["status"]: r["n"] for r in rows}}

    # --- votes ------------------------------------------------------------------

    async def vote(self, suggestion_id: int, user_id: int, vote: int) -> tuple[int, int, int]:
        """Press a vote button: the same vote again takes it back, the other one switches.
        Returns (up, down, this user's vote now: 1, -1 or 0)."""
        async with self.pool.acquire() as conn, conn.transaction():
            current = await conn.fetchval(
                "SELECT vote FROM suggestions.votes WHERE suggestion_id = $1 AND user_id = $2 FOR UPDATE",
                suggestion_id, user_id,
            )
            if current == vote:
                await conn.execute("DELETE FROM suggestions.votes WHERE suggestion_id = $1 AND user_id = $2",
                                   suggestion_id, user_id)
                now = 0
            else:
                await conn.execute(
                    "INSERT INTO suggestions.votes (suggestion_id, user_id, vote) VALUES ($1, $2, $3) "
                    "ON CONFLICT (suggestion_id, user_id) DO UPDATE SET vote = EXCLUDED.vote",
                    suggestion_id, user_id, vote,
                )
                now = vote
            up, down = await self._tally(conn, suggestion_id)
        return up, down, now

    async def tally(self, suggestion_id: int) -> tuple[int, int]:
        async with self.pool.acquire() as conn:
            return await self._tally(conn, suggestion_id)

    @staticmethod
    async def _tally(conn, suggestion_id: int) -> tuple[int, int]:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE vote = 1) AS up, COUNT(*) FILTER (WHERE vote = -1) AS down "
            "FROM suggestions.votes WHERE suggestion_id = $1",
            suggestion_id,
        )
        return row["up"], row["down"]

    async def forget_message(self, message_id: int) -> bool:
        """The suggestion's message was deleted by hand: drop the suggestion and its votes."""
        status = await self.pool.execute("DELETE FROM suggestions.suggestions WHERE message_id = $1", message_id)
        return affected(status) > 0
