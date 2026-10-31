"""Ticket settings and tickets (schema `tickets`) on the bot's shared pool."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import asyncpg

from core.db import affected

SETTINGS_COLUMNS = frozenset({
    "enabled", "category_id", "staff_role_ids", "log_channel_id", "panel_channel_id", "panel_message_id",
    "max_open", "ask_reason", "opening_embed", "button_label",
})
DEFAULTS: dict[str, Any] = {
    "enabled": False, "category_id": None, "staff_role_ids": [], "log_channel_id": None,
    "panel_channel_id": None, "panel_message_id": None, "max_open": 1, "ask_reason": True,
    "opening_embed": None, "button_label": "Open a ticket", "counter": 0,
}


def _row(record: asyncpg.Record | None) -> SimpleNamespace | None:
    return SimpleNamespace(**dict(record)) if record is not None else None


class TicketDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

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

    async def create(self, guild_id: int, number: int, channel_id: int, opener_id: int, reason: str | None) -> SimpleNamespace:
        return _row(await self.pool.fetchrow(
            "INSERT INTO tickets.tickets (guild_id, number, channel_id, opener_id, reason) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            guild_id, number, channel_id, opener_id, reason,
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
