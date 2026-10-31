"""Self-role storage (schema `selfroles`): the role behind each option, and where each picker is posted."""

from __future__ import annotations

import asyncpg


class SelfRolesDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def roles(self, guild_id: int, menu: str) -> dict[str, int]:
        rows = await self.pool.fetch("SELECT key, role_id FROM selfroles.roles WHERE guild_id = $1 AND menu = $2", guild_id, menu)
        return {r["key"]: r["role_id"] for r in rows}

    async def set_role(self, guild_id: int, menu: str, key: str, role_id: int) -> None:
        await self.pool.execute(
            "INSERT INTO selfroles.roles (guild_id, menu, key, role_id) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, menu, key) DO UPDATE SET role_id = EXCLUDED.role_id",
            guild_id, menu, key, role_id,
        )

    async def location(self, guild_id: int, menu: str) -> tuple[int, int] | None:
        row = await self.pool.fetchrow(
            "SELECT channel_id, message_id FROM selfroles.menus WHERE guild_id = $1 AND menu = $2", guild_id, menu
        )
        return (row["channel_id"], row["message_id"]) if row else None

    async def set_location(self, guild_id: int, menu: str, channel_id: int, message_id: int) -> None:
        await self.pool.execute(
            "INSERT INTO selfroles.menus (guild_id, menu, channel_id, message_id) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, menu) DO UPDATE SET channel_id = EXCLUDED.channel_id, message_id = EXCLUDED.message_id",
            guild_id, menu, channel_id, message_id,
        )

    async def set_menu_roles(self, guild_id: int, menu: str, role_ids: list[int]) -> None:
        """Replace a menu's options with these roles (option key = the role's ID)."""
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM selfroles.roles WHERE guild_id = $1 AND menu = $2", guild_id, menu)
            await conn.executemany(
                "INSERT INTO selfroles.roles (guild_id, menu, key, role_id) VALUES ($1, $2, $3, $4)",
                [(guild_id, menu, str(r), r) for r in role_ids],
            )
