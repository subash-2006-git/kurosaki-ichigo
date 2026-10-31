"""Records which invite each member joined with, and /invites to see who brought in whom."""

from __future__ import annotations

import logging
from collections import Counter
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.ui import style
from core.ui.theme import brand

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

TOP = 10


def top_inviters(joins, is_member) -> list[tuple[int, int, int]]:
    """(inviter_id, invited, still here), most invited first."""
    invited = Counter(j.inviter_id for j in joins)
    here = Counter(j.inviter_id for j in joins if is_member(j.user_id))
    return sorted(((i, n, here[i]) for i, n in invited.items()), key=lambda row: (-row[1], -row[2], row[0]))


class Invites(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            await self.bot.invite_tracker.load(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.bot.invite_tracker.load(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        self.bot.invite_tracker.created(invite)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        self.bot.invite_tracker.deleted(invite)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        result = await self.bot.invite_tracker.lookup(member)
        try:
            await self.bot.invites_db.record_join(member.guild.id, member.id, result.inviter_id, result.code)
        except Exception:
            log.exception("Saving the invite for %s in guild %s failed", member.id, member.guild.id)

    @app_commands.command(name="invites", description="Who a member brought in with their invites, or the top inviters")
    @app_commands.describe(member="Leave empty for the server's top inviters")
    @app_commands.guild_only()
    async def invites(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        guild = interaction.guild
        if member is not None:
            invited = await self.bot.invites_db.invited_by(guild.id, member.id)
            here = sum(1 for u in invited if guild.get_member(u))
            joined = await self.bot.invites_db.join_of(guild.id, member.id)
            text = f"{member.mention} has invited **{len(invited)}** member(s), **{here}** still here."
            if joined is not None:
                text += f"\nThey joined with {f'an invite from <@{joined.inviter_id}>' if joined.inviter_id else 'an unknown invite'}."
            embed = style.info("🔗 Invites", text)
        else:
            rows = top_inviters(await self.bot.invites_db.all_joins(guild.id), lambda u: guild.get_member(u) is not None)
            lines = [f"`{i + 1}.` <@{inviter}> · **{n}** invited ({here} still here)"
                     for i, (inviter, n, here) in enumerate(rows[:TOP])]
            embed = style.info("🔗 Top inviters", "\n".join(lines) or "No invites recorded yet.")
        if not guild.me.guild_permissions.manage_guild:
            embed.set_footer(text="I need Manage Server to see which invite people use.")
        await interaction.response.send_message(embed=brand(embed, guild), allowed_mentions=discord.AllowedMentions.none())
