"""Guild settings rows and XP log retention (from general-bot's system cog; /ping, /status and errors live in core)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks

from core.bot import Bot as CommunityBot
from features.community.repositories import GuildRepo, XpLogRepo

log = logging.getLogger(__name__)

XP_LOG_RETENTION = timedelta(days=30)


class Housekeeping(commands.Cog):
    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.prune_xp_log.start()

    async def cog_unload(self) -> None:
        self.prune_xp_log.cancel()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # on_ready can fire again after reconnects; this is safe to repeat.
        async with self.bot.community_db.session() as session:
            await GuildRepo(session).ensure_many([guild.id for guild in self.bot.guilds])

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        async with self.bot.community_db.session() as session:
            await GuildRepo(session).get_or_create(guild.id)

    @tasks.loop(hours=24)
    async def prune_xp_log(self) -> None:
        cutoff = datetime.now(timezone.utc) - XP_LOG_RETENTION
        try:
            async with self.bot.community_db.session() as session:
                deleted = await XpLogRepo(session).prune(cutoff)
        except Exception:
            log.exception("Failed to prune xp_log")
            return
        if deleted:
            log.info("Pruned %d xp_log row(s) older than %s days", deleted, XP_LOG_RETENTION.days)


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(Housekeeping(bot))
