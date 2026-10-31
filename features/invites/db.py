"""Invite tracking storage (schema `invites`)."""

from __future__ import annotations

from types import SimpleNamespace

import asyncpg


class InvitesDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def record_join(self, guild_id: int, user_id: int, inviter_id: int | None, code: str | None) -> None:
        await self.pool.execute(
            "INSERT INTO invites.joins (guild_id, user_id, inviter_id, code) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET inviter_id = EXCLUDED.inviter_id, code = EXCLUDED.code, "
            "joined_at = NOW()",
            guild_id, user_id, inviter_id, code,
        )

    async def invited_by(self, guild_id: int, inviter_id: int) -> list[int]:
        rows = await self.pool.fetch(
            "SELECT user_id FROM invites.joins WHERE guild_id = $1 AND inviter_id = $2", guild_id, inviter_id
        )
        return [r["user_id"] for r in rows]

    async def join_of(self, guild_id: int, user_id: int) -> SimpleNamespace | None:
        row = await self.pool.fetchrow("SELECT * FROM invites.joins WHERE guild_id = $1 AND user_id = $2", guild_id, user_id)
        return SimpleNamespace(**dict(row)) if row else None

    async def all_joins(self, guild_id: int) -> list[SimpleNamespace]:
        rows = await self.pool.fetch(
            "SELECT * FROM invites.joins WHERE guild_id = $1 AND inviter_id IS NOT NULL", guild_id
        )
        return [SimpleNamespace(**dict(r)) for r in rows]
