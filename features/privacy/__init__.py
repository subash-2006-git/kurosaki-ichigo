"""/mydata: members see what the bot stores about them and can delete it themselves.

Deleted: XP and levels, the XP history, nickname records, starboard entries (and their board posts).
Kept: moderation records (cases, notes, reports) and ticket records, which servers need to enforce
their rules. PRIVACY.txt says the same.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from core.ui import style
from core.ui.theme import brand

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

DELETABLE = {
    "XP and levels": "SELECT COUNT(*) FROM community.member_levels WHERE user_id = $1",
    "XP history entries": "SELECT COUNT(*) FROM community.xp_log WHERE user_id = $1",
    "Saved original nicknames": "SELECT COUNT(*) FROM nickname.original_nicknames WHERE user_id = $1",
    "Nickname change records": "SELECT COUNT(*) FROM nickname.change_log WHERE user_id = $1",
    "Starboard posts": "SELECT COUNT(*) FROM starboard.entries WHERE author_id = $1",
}
KEPT = {
    "Moderation cases": "SELECT COUNT(*) FROM moderation.cases WHERE user_id = $1",
    "Staff notes": "SELECT COUNT(*) FROM moderation.notes WHERE user_id = $1",
    "Reports": "SELECT COUNT(*) FROM moderation.reports WHERE target_id = $1 OR reporter_id = $1",
    "Tickets": "SELECT COUNT(*) FROM tickets.tickets WHERE opener_id = $1",
}
DELETE = (
    "DELETE FROM community.member_levels WHERE user_id = $1",
    "DELETE FROM community.xp_log WHERE user_id = $1",
    "DELETE FROM nickname.original_nicknames WHERE user_id = $1",
    "DELETE FROM nickname.change_log WHERE user_id = $1",
    "DELETE FROM starboard.entries WHERE author_id = $1",
)


async def summary(pool: asyncpg.Pool, user_id: int) -> tuple[dict[str, int], dict[str, int]]:
    async with pool.acquire() as conn:
        deletable = {label: await conn.fetchval(sql, user_id) for label, sql in DELETABLE.items()}
        kept = {label: await conn.fetchval(sql, user_id) for label, sql in KEPT.items()}
    return deletable, kept


async def delete_user_data(pool: asyncpg.Pool, user_id: int) -> list[tuple[int, int]]:
    """Delete everything deletable in one transaction. Returns the (channel, message) of starboard posts to remove."""
    async with pool.acquire() as conn, conn.transaction():
        posts = [(r["guild_id"], r["star_message_id"]) for r in
                 await conn.fetch("SELECT guild_id, star_message_id FROM starboard.entries WHERE author_id = $1", user_id)]
        for sql in DELETE:
            await conn.execute(sql, user_id)
    return posts


def summary_embed(deletable: dict[str, int], kept: dict[str, int], guild: discord.Guild | None) -> discord.Embed:
    lines = [f"• {label}: **{n}**" for label, n in deletable.items() if n] or ["Nothing."]
    kept_lines = [f"• {label}: **{n}**" for label, n in kept.items() if n] or ["Nothing."]
    embed = style.info("🔒 Your data", "What this bot stores about you, across every server it's in.")
    embed.add_field(name="You can delete", value="\n".join(lines), inline=False)
    embed.add_field(name="Kept (servers need these to enforce their rules)", value="\n".join(kept_lines), inline=False)
    embed.set_footer(text="Message text is never stored. Log channels and transcripts live in each server, not in the bot.")
    return brand(embed, guild)


class Privacy(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @app_commands.command(name="mydata", description="See what this bot stores about you, and delete it.")
    async def mydata(self, interaction: discord.Interaction) -> None:
        deletable, kept = await summary(self.bot.db.pool, interaction.user.id)
        view = discord.ui.View(timeout=120)
        delete = discord.ui.Button(label="Delete my data", emoji="🗑️", style=discord.ButtonStyle.danger,
                                   disabled=not any(deletable.values()))

        async def ask(button_interaction: discord.Interaction) -> None:
            confirm = discord.ui.View(timeout=60)
            yes = discord.ui.Button(label="Yes, delete it", style=discord.ButtonStyle.danger)

            async def do_delete(confirm_interaction: discord.Interaction) -> None:
                posts = await delete_user_data(self.bot.db.pool, interaction.user.id)
                for guild_id, message_id in posts:
                    await self._remove_board_post(guild_id, message_id)
                log.info("User %s deleted their data (%d starboard posts)", interaction.user.id, len(posts))
                await confirm_interaction.response.edit_message(
                    embed=style.ok("Done. Your XP, levels, nickname records and starboard posts are deleted. "
                                   "Level roles you already have stay on your account; a server admin can remove them."),
                    view=None,
                )

            yes.callback = do_delete
            confirm.add_item(yes)
            await button_interaction.response.send_message(
                embed=style.warn("Delete your XP, levels, nickname records and starboard posts in **every** server? "
                                 "This can't be undone."),
                view=confirm, ephemeral=True,
            )

        delete.callback = ask
        view.add_item(delete)
        await interaction.response.send_message(embed=summary_embed(deletable, kept, interaction.guild), view=view, ephemeral=True)

    async def _remove_board_post(self, guild_id: int, message_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        settings = await self.bot.starboard_db.settings(guild_id)
        board = guild.get_channel(settings.channel_id) if settings.channel_id else None
        if isinstance(board, discord.TextChannel):
            try:
                await board.get_partial_message(message_id).delete()
            except discord.HTTPException:
                pass


async def setup(bot: Bot) -> None:
    await bot.add_cog(Privacy(bot))
