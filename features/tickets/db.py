"""Ticket settings and tickets (schema `tickets`) on the bot's shared pool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

from core.db import affected

SETTINGS_COLUMNS = frozenset({
    "enabled", "category_id", "staff_role_ids", "log_channel_id", "panel_channel_id", "panel_message_id",
    "max_open", "ask_reason", "opening_embed", "button_label", "idle_close_hours", "idle_warn_hours", "ask_rating",
})
TYPE_COLUMNS = frozenset({"label", "emoji", "staff_role_ids", "category_id"})
MAX_TYPES = 5  # one row of buttons
DEFAULTS: dict[str, Any] = {
    "enabled": False, "category_id": None, "staff_role_ids": [], "log_channel_id": None,
    "panel_channel_id": None, "panel_message_id": None, "max_open": 1, "ask_reason": True,
    "opening_embed": None, "button_label": "Open a ticket", "counter": 0,
    "idle_close_hours": 0, "idle_warn_hours": 12, "ask_rating": True,
}


def _row(record: asyncpg.Record | None) -> SimpleNamespace | None:
    return SimpleNamespace(**dict(record)) if record is not None else None


class TicketDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        # Whether any server closes idle tickets; None = not checked yet. Kept in memory so the
        # auto-close loop doesn't query (and wake Neon) when nobody uses it.
        self.idle_on: bool | None = None

    # --- settings ---------------------------------------------------------------

    async def settings(self, guild_id: int) -> SimpleNamespace:
        row = await self.pool.fetchrow("SELECT * FROM tickets.settings WHERE guild_id = $1", guild_id)
        return _row(row) if row else SimpleNamespace(guild_id=guild_id, **DEFAULTS)

    async def update_settings(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - SETTINGS_COLUMNS
        if unknown:
            raise ValueError(f"Unknown ticket settings: {', '.join(sorted(unknown))}")
        columns = list(fields)
        placeholders = ", ".join(f"${i}" for i in range(2, len(columns) + 2))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        if "idle_close_hours" in fields:
            self.idle_on = None
        return _row(await self.pool.fetchrow(
            f"INSERT INTO tickets.settings (guild_id, {', '.join(columns)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates} RETURNING *",
            guild_id, *fields.values(),
        ))

    # --- tickets ----------------------------------------------------------------

    async def next_number(self, guild_id: int) -> int:
        return await self.pool.fetchval(
            "INSERT INTO tickets.settings (guild_id, counter) VALUES ($1, 1) "
            "ON CONFLICT (guild_id) DO UPDATE SET counter = tickets.settings.counter + 1 RETURNING counter",
            guild_id,
        )

    async def create(self, guild_id: int, number: int, channel_id: int, opener_id: int, reason: str | None,
                     type_id: int | None = None) -> SimpleNamespace:
        return _row(await self.pool.fetchrow(
            "INSERT INTO tickets.tickets (guild_id, number, channel_id, opener_id, reason, type_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) RETURNING *",
            guild_id, number, channel_id, opener_id, reason, type_id,
        ))

    async def by_channel(self, channel_id: int) -> SimpleNamespace | None:
        return _row(await self.pool.fetchrow(
            "SELECT * FROM tickets.tickets WHERE channel_id = $1 ORDER BY id DESC LIMIT 1", channel_id
        ))

    async def open_for(self, guild_id: int, opener_id: int) -> list[SimpleNamespace]:
        rows = await self.pool.fetch(
            "SELECT * FROM tickets.tickets WHERE guild_id = $1 AND opener_id = $2 AND status = 'open' ORDER BY id",
            guild_id, opener_id,
        )
        return [_row(r) for r in rows]

    async def open_count(self, guild_id: int) -> int:
        return await self.pool.fetchval(
            "SELECT COUNT(*) FROM tickets.tickets WHERE guild_id = $1 AND status = 'open'", guild_id
        )

    async def claim(self, ticket_id: int, staff_id: int) -> bool:
        status = await self.pool.execute(
            "UPDATE tickets.tickets SET claimed_by = $2 WHERE id = $1 AND status = 'open' AND claimed_by IS NULL",
            ticket_id, staff_id,
        )
        return affected(status) > 0

    async def close(self, ticket_id: int, closed_by: int) -> bool:
        """Mark a ticket closed. False if it was already closed (someone else got there first)."""
        status = await self.pool.execute(
            "UPDATE tickets.tickets SET status = 'closed', closed_at = NOW(), closed_by = $2 WHERE id = $1 AND status = 'open'",
            ticket_id, closed_by,
        )
        return affected(status) > 0

    async def forget_channel(self, channel_id: int) -> None:
        """A ticket channel was deleted by hand: close its ticket so the member can open a new one."""
        await self.pool.execute(
            "UPDATE tickets.tickets SET status = 'closed', closed_at = NOW() WHERE channel_id = $1 AND status = 'open'",
            channel_id,
        )

    # --- types ------------------------------------------------------------------

    async def types(self, guild_id: int) -> list[SimpleNamespace]:
        rows = await self.pool.fetch("SELECT * FROM tickets.types WHERE guild_id = $1 ORDER BY id", guild_id)
        return [_row(r) for r in rows]

    async def type(self, guild_id: int, type_id: int) -> SimpleNamespace | None:
        return _row(await self.pool.fetchrow(
            "SELECT * FROM tickets.types WHERE guild_id = $1 AND id = $2", guild_id, type_id
        ))

    async def add_type(self, guild_id: int, label: str, emoji: str | None) -> SimpleNamespace | None:
        """None when the server already has the most types allowed."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('tickets.types'), hashtext($1::text))", str(guild_id))
            if await conn.fetchval("SELECT COUNT(*) FROM tickets.types WHERE guild_id = $1", guild_id) >= MAX_TYPES:
                return None
            return _row(await conn.fetchrow(
                "INSERT INTO tickets.types (guild_id, label, emoji) VALUES ($1, $2, $3) RETURNING *", guild_id, label, emoji
            ))

    async def update_type(self, guild_id: int, type_id: int, **fields: Any) -> SimpleNamespace | None:
        unknown = set(fields) - TYPE_COLUMNS
        if unknown:
            raise ValueError(f"Unknown ticket type fields: {', '.join(sorted(unknown))}")
        sets = ", ".join(f"{c} = ${i}" for i, c in enumerate(fields, start=3))
        return _row(await self.pool.fetchrow(
            f"UPDATE tickets.types SET {sets} WHERE guild_id = $1 AND id = $2 RETURNING *",
            guild_id, type_id, *fields.values(),
        ))

    async def remove_type(self, guild_id: int, type_id: int) -> bool:
        status = await self.pool.execute("DELETE FROM tickets.types WHERE guild_id = $1 AND id = $2", guild_id, type_id)
        return affected(status) > 0

    # --- auto-close -------------------------------------------------------------

    async def idle_candidates(self) -> list[SimpleNamespace]:
        """Open tickets in servers that close idle tickets, with that server's limits."""
        rows = await self.pool.fetch(
            "SELECT t.*, s.idle_close_hours, s.idle_warn_hours FROM tickets.tickets t "
            "JOIN tickets.settings s USING (guild_id) WHERE t.status = 'open' AND s.idle_close_hours > 0"
        )
        return [_row(r) for r in rows]

    async def any_idle_close(self) -> bool:
        return await self.pool.fetchval("SELECT EXISTS (SELECT 1 FROM tickets.settings WHERE idle_close_hours > 0)")

    async def set_idle_warning(self, ticket_id: int, message_id: int | None) -> None:
        await self.pool.execute("UPDATE tickets.tickets SET idle_warning_id = $2 WHERE id = $1", ticket_id, message_id)

    # --- ratings ----------------------------------------------------------------

    async def rate(self, ticket_id: int, opener_id: int, rating: int) -> bool:
        """Save the opener's rating once. False if it isn't theirs, isn't closed, or was already rated."""
        status = await self.pool.execute(
            "UPDATE tickets.tickets SET rating = $3 WHERE id = $1 AND opener_id = $2 AND status = 'closed' AND rating IS NULL",
            ticket_id, opener_id, rating,
        )
        return affected(status) > 0

    async def rating_stats(self, guild_id: int) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
        """The server's overall average, and per staff member (who claimed the ticket), best first."""
        overall = _row(await self.pool.fetchrow(
            "SELECT COUNT(rating) AS count, AVG(rating)::float AS average FROM tickets.tickets WHERE guild_id = $1",
            guild_id,
        ))
        staff = await self.pool.fetch(
            "SELECT claimed_by AS staff_id, COUNT(rating) AS count, AVG(rating)::float AS average FROM tickets.tickets "
            "WHERE guild_id = $1 AND claimed_by IS NOT NULL AND rating IS NOT NULL "
            "GROUP BY claimed_by ORDER BY AVG(rating) DESC, COUNT(rating) DESC LIMIT 10",
            guild_id,
        )
        return overall, [_row(r) for r in staff]

    async def by_id(self, ticket_id: int) -> SimpleNamespace | None:
        return _row(await self.pool.fetchrow("SELECT * FROM tickets.tickets WHERE id = $1", ticket_id))
