"""/leaderboard: the server's XP ranking, 10 per page with Previous/Next buttons."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from core.bot import Bot as CommunityBot
from core.interactions import EPHEMERAL, reply
from core.ui.theme import brand
from features.community.services.leveling import LeaderboardEntry

log = logging.getLogger(__name__)

PER_PAGE = 10
VIEW_TIMEOUT = 120
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def page_count(total: int, per_page: int = PER_PAGE) -> int:
    return max(1, math.ceil(total / per_page))


def leaderboard_lines(entries: list[LeaderboardEntry], name_for: Callable[[LeaderboardEntry], str]) -> list[str]:
    lines = []
    for entry in entries:
        prefix = MEDALS.get(entry.position, f"`#{entry.position}`")
        lines.append(f"{prefix} **{name_for(entry)}** · Level {entry.level:,} · {entry.total_xp:,} XP")
    return lines


def display_name(guild: discord.Guild, entry: LeaderboardEntry) -> str:
    member = guild.get_member(entry.user_id)
    if member is not None:
        return escape_markdown(member.display_name)
    return f"{escape_markdown(entry.name or f'User {entry.user_id}')} (left)"


class LeaderboardView(discord.ui.View):
    def __init__(self, cog: LeaderboardCog, owner_id: int, page: int, pages: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT)
        self.cog, self.owner_id, self.page, self.pages = cog, owner_id, page, pages
        self.message: discord.InteractionMessage | None = None
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await reply(interaction, "Run `/leaderboard` to browse it yourself.", ephemeral=True)
        return False

    async def _show(self, interaction: discord.Interaction, page: int) -> None:
        assert interaction.guild is not None
        # Acknowledge the click first: building the page reads the database.
        await interaction.response.defer()
        embed, self.pages = await self.cog.build_page(interaction.guild, page, self.owner_id)
        self.page = min(page, self.pages - 1)
        self._update_buttons()
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._show(interaction, self.page - 1)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._show(interaction, self.page + 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass  # message deleted, or the interaction token expired


class LeaderboardCog(commands.Cog):
    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot

    async def build_page(self, guild: discord.Guild, page: int, viewer_id: int) -> tuple[discord.Embed, int]:
        """The embed for a 0-based page (clamped to the last page), and the number of pages."""
        entries, total = await self.bot.leveling.leaderboard(guild.id, page, PER_PAGE)
        pages = page_count(total)
        if page >= pages and total:
            page = pages - 1
            entries, total = await self.bot.leveling.leaderboard(guild.id, page, PER_PAGE)

        embed = discord.Embed(title=f"🏆 {guild.name} leaderboard")
        if entries:
            embed.description = "\n".join(leaderboard_lines(entries, lambda entry: display_name(guild, entry)))
        else:
            embed.description = "Nobody has earned XP yet. Start chatting!"

        footer = f"Page {page + 1}/{pages} · {total:,} ranked member{'s' if total != 1 else ''}"
        viewer = await self.bot.leveling.rank(guild.id, viewer_id)
        if viewer is not None:
            footer += f" · Your rank: #{viewer.position:,}"
        embed.set_footer(text=footer)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        return brand(embed, guild), pages

    @app_commands.command(name="leaderboard", description="Show the server's XP leaderboard.", extras={EPHEMERAL: False})
    @app_commands.describe(page="Page to start on")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction, page: app_commands.Range[int, 1, 10_000] = 1) -> None:
        guild = interaction.guild
        assert guild is not None
        config = await self.bot.guild_config.get(guild.id)
        if not config.leveling_enabled:
            await reply(interaction,
                "Leveling isn't turned on in this server. A server manager can enable it with `/leveling enable`.",
                ephemeral=True,
            )
            return

        embed, pages = await self.build_page(guild, page - 1, interaction.user.id)
        start = min(page - 1, pages - 1)
        if pages == 1:
            await reply(interaction, embed=embed)
            return
        view = LeaderboardView(self, interaction.user.id, start, pages)
        await reply(interaction, embed=embed, view=view)
        view.message = await interaction.original_response()


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(LeaderboardCog(bot))
