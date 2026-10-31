"""/starboard top: the members and messages with the most stars on the board."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.ui import style
from core.ui.theme import brand

if TYPE_CHECKING:
    from core.bot import Bot

MEDALS = ["🥇", "🥈", "🥉"]


def leaderboard_embed(authors, messages, emoji: str, guild_id: int) -> discord.Embed:
    embed = style.info(f"{emoji} Starboard leaderboard")
    if not authors:
        embed.description = "Nothing on the board yet."
        return embed
    lines = [f"{MEDALS[i] if i < len(MEDALS) else f'`{i + 1}.`'} <@{a.author_id}> · **{a.stars}** {emoji} "
             f"({a.posts} post{'s' if a.posts != 1 else ''})" for i, a in enumerate(authors)]
    embed.add_field(name="Most stars", value="\n".join(lines), inline=False)
    links = [f"**{m.stars}** {emoji} · <@{m.author_id}> · "
             f"[jump](https://discord.com/channels/{guild_id}/{m.channel_id}/{m.message_id})" for m in messages]
    embed.add_field(name="Top messages", value="\n".join(links), inline=False)
    return embed


class StarboardTop(commands.Cog):
    starboard = app_commands.Group(name="starboard", description="Starboard", guild_only=True)

    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @starboard.command(name="top", description="The members and messages with the most stars")
    async def top(self, interaction: discord.Interaction) -> None:
        db = self.bot.starboard_db
        settings = await db.settings(interaction.guild_id)
        embed = leaderboard_embed(await db.top_authors(interaction.guild_id), await db.top_messages(interaction.guild_id),
                                  settings.emoji, interaction.guild_id)
        await interaction.response.send_message(embed=brand(embed, interaction.guild),
                                                allowed_mentions=discord.AllowedMentions.none())
