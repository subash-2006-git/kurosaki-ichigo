"""Quote images: right-click a message → Apps → "Make a quote image", or reply to it mentioning the bot.

The image's buttons (Colour, Flip, Light/Dark, Delete) are DynamicItems that carry everything they
need in their custom_id ("quote:<action>:<style bits>:<requester>:<channel>:<message>"), so they keep
working after restarts: the original message is fetched again and the image re-rendered.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from core.ui import style
from features.quotes.render import QuoteStyle, clean_text, render_quote

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

COOLDOWN = 10.0
STYLE_ACTIONS = {"colour": ("Colour", "🎨"), "flip": ("Flip", "↔️"), "light": ("Light / Dark", "🌗")}


class QuotesDB:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._cache: dict[int, bool] = {}

    async def enabled(self, guild_id: int) -> bool:
        if guild_id not in self._cache:
            value = await self.pool.fetchval("SELECT enabled FROM quotes.settings WHERE guild_id = $1", guild_id)
            self._cache[guild_id] = True if value is None else value
        return self._cache[guild_id]

    async def set_enabled(self, guild_id: int, enabled: bool) -> None:
        await self.pool.execute(
            "INSERT INTO quotes.settings (guild_id, enabled) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET enabled = EXCLUDED.enabled", guild_id, enabled,
        )
        self._cache[guild_id] = enabled


def quote_text(message: discord.Message) -> str:
    guild = message.guild

    def user(user_id: int) -> str | None:
        found = guild.get_member(user_id) if guild else None
        found = found or next((u for u in message.mentions if u.id == user_id), None)
        return found.display_name if found else None

    def role(role_id: int) -> str | None:
        found = guild.get_role(role_id) if guild else None
        return found.name if found else None

    def channel(channel_id: int) -> str | None:
        found = guild.get_channel(channel_id) if guild else None
        return found.name if found else None

    return clean_text(message.content, user=user, role=role, channel=channel)


async def make_image(message: discord.Message, quote_style: QuoteStyle) -> discord.File:
    author = message.author
    try:
        avatar = await author.display_avatar.replace(size=512, format="png").read()
    except discord.DiscordException:
        avatar = None
    png = await asyncio.to_thread(render_quote, quote_text(message), author.display_name, author.name, avatar, quote_style)
    return discord.File(io.BytesIO(png), filename="quote.png")


def quote_view(quote_style: QuoteStyle, requester_id: int, channel_id: int, message_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in (*STYLE_ACTIONS, "delete"):
        view.add_item(QuoteButton(action, quote_style.bits, requester_id, channel_id, message_id))
    return view


class QuoteButton(discord.ui.DynamicItem[discord.ui.Button],
                  template=r"quote:(?P<action>colour|flip|light|delete):(?P<bits>\d):(?P<req>\d+):(?P<ch>\d+):(?P<msg>\d+)"):
    def __init__(self, action: str, bits: int, requester_id: int, channel_id: int, message_id: int) -> None:
        label, emoji = STYLE_ACTIONS.get(action, ("Delete", "🗑️"))
        super().__init__(discord.ui.Button(
            label=label, emoji=emoji,
            style=discord.ButtonStyle.danger if action == "delete" else discord.ButtonStyle.secondary,
            custom_id=f"quote:{action}:{bits}:{requester_id}:{channel_id}:{message_id}",
        ))
        self.action, self.bits = action, bits
        self.requester_id, self.channel_id, self.message_id = requester_id, channel_id, message_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> QuoteButton:
        return cls(match["action"], int(match["bits"]), int(match["req"]), int(match["ch"]), int(match["msg"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.client.get_channel(self.channel_id)
        original: discord.Message | None = None
        if isinstance(channel, discord.abc.Messageable):
            try:
                original = await channel.fetch_message(self.message_id)
            except discord.HTTPException:
                original = None
        member = interaction.user
        allowed = member.id == self.requester_id or (original is not None and member.id == original.author.id) or (
            isinstance(member, discord.Member) and interaction.channel is not None
            and interaction.channel.permissions_for(member).manage_messages
        )
        if not allowed:
            await interaction.response.send_message(
                embed=style.fail("Only the person who made this quote, or the person quoted, can change it."), ephemeral=True)
            return
        if self.action == "delete":
            await interaction.response.defer()
            await interaction.message.delete()
            return
        if original is None:
            await interaction.response.send_message(embed=style.warn("The original message is gone, so this quote can't change."),
                                                    ephemeral=True)
            return
        await interaction.response.defer()
        new_style = QuoteStyle.from_bits(self.bits).toggled(self.action)
        image = await make_image(original, new_style)
        await interaction.edit_original_response(
            attachments=[image], view=quote_view(new_style, self.requester_id, self.channel_id, self.message_id)
        )


class Quotes(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._last_use: dict[int, float] = {}
        self.menu = app_commands.ContextMenu(name="Make a quote image", callback=self.quote_menu)
        self.menu.guild_only = True
        bot.tree.add_command(self.menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.menu.name, type=self.menu.type)

    def _cooling_down(self, user_id: int) -> float:
        now = time.monotonic()
        wait = COOLDOWN - (now - self._last_use.get(user_id, 0))
        if wait > 0:
            return wait
        self._last_use[user_id] = now
        return 0

    async def _problem(self, guild_id: int, message: discord.Message, user_id: int) -> str | None:
        if not await self.bot.quotes_db.enabled(guild_id):
            return "Quote images are turned off in this server."
        if not quote_text(message):
            return "Only messages with text can be made into a quote."
        if wait := self._cooling_down(user_id):
            return f"Slow down! Try again in {wait:.0f}s."
        return None

    async def quote_menu(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if problem := await self._problem(interaction.guild_id, message, interaction.user.id):
            await interaction.response.send_message(embed=style.warn(problem), ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        image = await make_image(message, QuoteStyle())
        await interaction.followup.send(file=image, view=quote_view(QuoteStyle(), interaction.user.id, message.channel.id, message.id))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Reply to a message and mention the bot. The mention has to be typed: Discord's automatic
        # reply ping (when replying to the bot's own messages) doesn't count.
        if message.guild is None or message.author.bot or message.reference is None or self.bot.user is None:
            return
        if f"<@{self.bot.user.id}>" not in message.content and f"<@!{self.bot.user.id}>" not in message.content:
            return
        target = message.reference.resolved
        if not isinstance(target, discord.Message):
            try:
                target = await message.channel.fetch_message(message.reference.message_id)
            except discord.HTTPException:
                return
        if problem := await self._problem(message.guild.id, target, message.author.id):
            await message.reply(embed=style.warn(problem), mention_author=False, delete_after=8)
            return
        async with message.channel.typing():
            image = await make_image(target, QuoteStyle())
        await message.reply(file=image, view=quote_view(QuoteStyle(), message.author.id, target.channel.id, target.id),
                            mention_author=False)


def status_line(enabled: bool) -> str:
    return f"{style.ON} On · right-click a message → Apps → Make a quote image" if enabled else f"{style.OFF} Off"

