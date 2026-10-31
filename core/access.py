"""Who may use the bot's commands and the control panel, per server.

With OWNER_ONLY on (the default), in each server that's the server owner and the members they pick
in the panel (🔐 Access), plus the bot's operators everywhere: the user IDs in OWNER_IDS, or the
Discord application's owner/team when OWNER_IDS is empty. In DMs only the operators can.

Only the server owner and the operators can change who's on a server's list. Buttons members use
(opening tickets, self-role pickers, votes, ratings…) aren't commands and keep working for everyone.
"""

from __future__ import annotations

import asyncpg
import discord
from discord.ext import commands

from core import config

NOT_ALLOWED = "Only the server owner and the members they allow can use this bot's commands."
MAX_MEMBERS = 25  # one member picker


class AccessStore:
    """Each server's allowed members (table core.access), cached: every command checks it."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._cache: dict[int, frozenset[int]] = {}

    async def members(self, guild_id: int) -> frozenset[int]:
        cached = self._cache.get(guild_id)
        if cached is None:
            ids = await self.pool.fetchval("SELECT user_ids FROM core.access WHERE guild_id = $1", guild_id)
            cached = self._cache[guild_id] = frozenset(ids or ())
        return cached

    async def set_members(self, guild_id: int, user_ids: list[int]) -> None:
        if len(user_ids) > MAX_MEMBERS:
            raise ValueError(f"A server can allow at most {MAX_MEMBERS} members.")
        await self.pool.execute(
            "INSERT INTO core.access (guild_id, user_ids) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET user_ids = EXCLUDED.user_ids",
            guild_id, user_ids,
        )
        self._cache.pop(guild_id, None)


async def is_operator(bot: commands.Bot, user: discord.abc.Snowflake) -> bool:
    return await bot.is_owner(user)  # OWNER_IDS, or the application's owner/team


async def can_manage(bot: commands.Bot, user: discord.abc.Snowflake, guild: discord.Guild | None) -> bool:
    """Whether this user may change who can use the bot in this server."""
    return (guild is not None and user.id == guild.owner_id) or await is_operator(bot, user)


async def is_allowed(bot: commands.Bot, user: discord.abc.Snowflake, guild: discord.Guild | None) -> bool:
    if not config.OWNER_ONLY or await can_manage(bot, user, guild):
        return True
    return guild is not None and user.id in await bot.access.members(guild.id)
