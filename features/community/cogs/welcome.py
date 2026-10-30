"""Welcome messages: the on_member_join listener and the /welcome commands."""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from core.bot import Bot as CommunityBot
from features.community.cogs.greeting_commands import GreetingGroup
from features.community.services.greeting import build_greeting
from features.community.utils.checks import resolve_channel

log = logging.getLogger(__name__)


class WelcomeCog(commands.Cog):
    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot
        self.group = GreetingGroup(bot, "welcome")

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.group)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.group.name)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        try:
            config = await self.bot.guild_config.get(guild.id)
            if not config.welcome_enabled or await self.bot.member_turned_away(member):
                return

            channel, problem = resolve_channel(guild, config.welcome_channel_id)
            if channel is None or problem:
                log.warning("Welcome not sent in guild %s: %s", guild.id, problem)
                return

            message = await build_greeting(
                member, "welcome", config, self.bot.media, self.bot.http_session,
                brand=lambda embed: self.bot.theme.brand(embed, member.guild),
            )
            await channel.send(**message.send_kwargs())
            log.info("Welcomed %s in guild %s", member.id, guild.id)
        except discord.HTTPException as exc:
            log.warning("Welcome failed in guild %s: %s", guild.id, exc)
        except Exception:
            log.exception("Welcome failed in guild %s", guild.id)


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(WelcomeCog(bot))
