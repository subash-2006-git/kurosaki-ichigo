"""Goodbye messages: the on_member_remove listener, the optional DM, and the /goodbye commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from core.bot import Bot as CommunityBot
from features.community.cogs.greeting_commands import GreetingGroup
from features.community.services.greeting import build_greeting, send_goodbye_dm
from features.community.services.guild_config import GuildConfig
from features.community.utils.checks import resolve_channel

log = logging.getLogger(__name__)


class GoodbyeCog(commands.Cog):
    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot
        self.group = GreetingGroup(bot, "goodbye")

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.group.name)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # Fires for leaves, kicks and bans alike.
        guild = member.guild
        if self.bot.user is not None and member.id == self.bot.user.id:
            return
        try:
            if await self.bot.member_turned_away(member, leaving=True):
                return  # anti-raid kicked them; no goodbye card or DM per raider
            config = await self.bot.guild_config.get(guild.id)
            if config.goodbye_enabled:
                await self._post_goodbye(member, config)
            if config.goodbye_dm_enabled:
                await send_goodbye_dm(member, config)
        except Exception:
            log.exception("Goodbye failed in guild %s", guild.id)

    async def _post_goodbye(self, member: discord.Member, config: GuildConfig) -> None:
        guild = member.guild
        channel, problem = resolve_channel(guild, config.goodbye_channel_id)
        if channel is None or problem:
            log.warning("Goodbye not sent in guild %s: %s", guild.id, problem)
            return
        try:
            message = await build_greeting(
                member, "goodbye", config, self.bot.media, self.bot.http_session,
                brand=lambda embed: self.bot.theme.brand(embed, member.guild),
            )
            await channel.send(**message.send_kwargs())
            log.info("Said goodbye to %s in guild %s", member.id, guild.id)
        except discord.HTTPException as exc:
            # Logged here so a failed channel post doesn't stop the DM.
            log.warning("Goodbye failed in guild %s: %s", guild.id, exc)


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(GoodbyeCog(bot))
