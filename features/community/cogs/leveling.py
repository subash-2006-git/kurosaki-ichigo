"""Message XP (on_message), level roles for rejoining members, and /rank.

The /leveling admin commands live in leveling_admin.py.
"""

from __future__ import annotations

import asyncio
import io
import logging
import random

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import Bot as CommunityBot
from features.community.models import XpSource
from features.community.services.cards import render_rank_card
from features.community.services.greeting import avatar_bytes
from features.community.services.leveling import RankInfo, progress
from features.community.utils.cooldowns import Cooldowns
from core.interactions import EPHEMERAL, defer, reply

log = logging.getLogger(__name__)

BAR_SEGMENTS = 12


def earns_message_xp(message: discord.Message) -> bool:
    """Only real member messages in servers: no bots, webhooks or system messages (joins, boosts, pins)."""
    return (
        message.guild is not None
        and not message.author.bot
        and message.webhook_id is None
        and not message.is_system()
    )


def progress_bar(fraction: float, segments: int = BAR_SEGMENTS) -> str:
    filled = min(segments, max(0, int(fraction * segments)))
    return "▰" * filled + "▱" * (segments - filled)


class LevelingCog(commands.Cog):
    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot
        self.cooldowns = Cooldowns()

    # ------------------------------------------------------------------ listeners

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not earns_message_xp(message):
            return
        assert message.guild is not None and isinstance(message.author, discord.Member)
        try:
            config = await self.bot.guild_config.get(message.guild.id)
            if not config.leveling_enabled:
                return
            if not self.cooldowns.try_acquire((message.guild.id, message.author.id), config.message_xp_cooldown):
                return

            amount = random.randint(config.message_xp_min, config.message_xp_max)
            result = await self.bot.leveling.award_xp(
                message.guild.id, message.author.id, amount, XpSource.MESSAGE, name=message.author.display_name
            )
        except Exception:
            log.exception("Message XP failed in guild %s", message.guild.id)
            return
        await self.bot.levelups.after_award(message.author, result, message.channel)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        # Rejoining members get their level roles back (their XP was kept when they left).
        if member.bot:
            return
        config = await self.bot.guild_config.get(member.guild.id)
        if config.leveling_enabled:
            await self.bot.roles.sync_member(member)

    # ------------------------------------------------------------------ /rank

    @app_commands.command(name="rank", description="Show your level and XP, or another member's.", extras={EPHEMERAL: False})
    @app_commands.describe(member="Whose rank to show (default: you)")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        target = member or interaction.user
        config = await self.bot.guild_config.get(interaction.guild.id)
        if not config.leveling_enabled:
            await reply(interaction, 
                "Leveling isn't turned on in this server. A server manager can enable it with `/leveling enable`.",
                ephemeral=True,
            )
            return
        if target.bot:
            await reply(interaction, "Bots don't earn XP.", ephemeral=True)
            return

        await defer(interaction, thinking=True)
        info = await self.bot.leveling.rank(interaction.guild.id, target.id)
        card = await rank_card(target, info)
        if card is not None:
            await interaction.followup.send(file=card)
        else:
            await interaction.followup.send(embed=rank_embed(target, info))


def accent_colour(member: discord.Member) -> tuple[int, int, int]:
    """The member's role colour, or blurple if their roles have none."""
    colour = member.colour if member.colour.value else discord.Colour.blurple()
    return colour.to_rgb()


async def rank_card(member: discord.Member, info: RankInfo | None) -> discord.File | None:
    """The /rank image, or None (logged) so the caller can fall back to an embed."""
    current = progress(info.total_xp if info else 0)
    avatar = await avatar_bytes(member)
    try:
        png = await asyncio.to_thread(
            render_rank_card,
            avatar,
            member.display_name,
            current.level,
            current.xp_into_level,
            current.xp_for_next,
            info.position if info else None,
            info.total_xp if info else 0,
            info.message_xp if info else 0,
            info.voice_xp if info else 0,
            accent_colour(member),
        )
    except Exception:
        log.warning("Failed to render rank card for %s in guild %s", member.id, member.guild.id, exc_info=True)
        return None
    return discord.File(io.BytesIO(png), filename="rank.png")


def rank_embed(member: discord.Member, info: RankInfo | None) -> discord.Embed:
    """info is None for a member with no XP yet."""
    embed = discord.Embed(colour=member.colour if member.colour.value else discord.Colour.blurple())
    embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)

    if info is None:
        embed.description = f"**Level 0**\n{progress_bar(0)} 0 / 100 XP\n-# No XP yet. Start chatting!"
        return embed

    current = progress(info.total_xp)
    embed.description = (
        f"**Level {current.level}** · Rank #{info.position:,} of {info.ranked_members:,}\n"
        f"{progress_bar(current.fraction)} {current.xp_into_level:,} / {current.xp_for_next:,} XP "
        f"to level {current.level + 1}"
    )
    embed.add_field(name="Total XP", value=f"{info.total_xp:,}")
    embed.add_field(name="Message XP", value=f"{info.message_xp:,}")
    embed.add_field(name="Voice XP", value=f"{info.voice_xp:,}")
    return embed


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(LevelingCog(bot))
