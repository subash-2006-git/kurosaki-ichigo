"""Quote images: right-click a message → Apps → "Make a quote image", reply to it mentioning the bot, or /quote.

Single quotes carry everything their buttons need in the custom_id
("quote:<action>:<style bits>:<requester>:<channel>:<message>"); multi-message quotes carry the first
message and how many ("quotes:<action>:<bits>:<requester>:<channel>:<first message>:<count>"). Both
are DynamicItems, so buttons keep working after restarts: the messages are fetched again and re-drawn.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from typing import TYPE_CHECKING

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from core.access import is_allowed
from core.ui import style
from features.quotes.render import QuoteLine, QuoteStyle, clean_text, render_conversation, render_quote

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

COOLDOWN = 10.0
MAX_MESSAGES = 5
STYLE_ACTIONS = {"colour": ("Colour", "🎨"), "flip": ("Flip", "↔️"), "light": ("Light / Dark", "🌗"), "tint": ("Tint", "🖌️")}
CONVO_ACTIONS = ("colour", "light", "tint")  # a conversation has no big avatar to flip


class QuotesDB:
    """On/off and the server's default style, cached."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._cache: dict[int, tuple[bool, int]] = {}

    async def _load(self, guild_id: int) -> tuple[bool, int]:
        if guild_id not in self._cache:
            row = await self.pool.fetchrow("SELECT enabled, default_bits FROM quotes.settings WHERE guild_id = $1", guild_id)
            self._cache[guild_id] = (row["enabled"], row["default_bits"]) if row else (True, 0)
        return self._cache[guild_id]

    def forget(self, guild_id: int) -> None:
        """Drop the cached settings (after a restore from backup)."""
        self._cache.pop(guild_id, None)

    async def enabled(self, guild_id: int) -> bool:
        return (await self._load(guild_id))[0]

    async def default_style(self, guild_id: int) -> QuoteStyle:
        return QuoteStyle.from_bits((await self._load(guild_id))[1])

    async def set_enabled(self, guild_id: int, enabled: bool) -> None:
        await self.pool.execute(
            "INSERT INTO quotes.settings (guild_id, enabled) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET enabled = EXCLUDED.enabled", guild_id, enabled,
        )
        self._cache.pop(guild_id, None)

    async def set_default_style(self, guild_id: int, quote_style: QuoteStyle) -> None:
        await self.pool.execute(
            "INSERT INTO quotes.settings (guild_id, default_bits) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET default_bits = EXCLUDED.default_bits", guild_id, quote_style.bits,
        )
        self._cache.pop(guild_id, None)


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


async def avatar_png(user: discord.abc.User, size: int = 512) -> bytes | None:
    try:
        return await user.display_avatar.replace(size=size, format="png").read()
    except discord.DiscordException:
        return None


def accent_for(client, guild: discord.Guild | None) -> int:
    return client.theme.get(guild).accent


async def make_image(message: discord.Message, quote_style: QuoteStyle, accent: int | None = None) -> discord.File:
    author = message.author
    png = await asyncio.to_thread(render_quote, quote_text(message), author.display_name, author.name,
                                  await avatar_png(author), quote_style, accent)
    return discord.File(io.BytesIO(png), filename="quote.png")


async def make_conversation(messages: list[discord.Message], quote_style: QuoteStyle, accent: int | None = None) -> discord.File:
    avatars: dict[int, bytes | None] = {}
    for message in messages:
        if message.author.id not in avatars:
            avatars[message.author.id] = await avatar_png(message.author, 128)
    lines = [QuoteLine(m.author.display_name, m.author.name, quote_text(m), avatars[m.author.id]) for m in messages]
    png = await asyncio.to_thread(render_conversation, lines, quote_style, accent)
    return discord.File(io.BytesIO(png), filename="quote.png")


def quotable(message: discord.Message) -> bool:
    return not message.author.bot and bool(message.content.strip())


MESSAGE_LINK = re.compile(r"(?:https?://)?(?:\w+\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


def parse_message_ref(text: str) -> tuple[int | None, int] | None:
    """A message link -> (channel_id, message_id); a bare ID -> (None, message_id); anything else -> None."""
    text = text.strip()
    if match := MESSAGE_LINK.search(text):
        return int(match[2]), int(match[3])
    if text.isdigit():
        return None, int(text)
    return None


async def find_message(interaction: discord.Interaction, text: str) -> discord.Message | str:
    """The message a link/ID points to, or a friendly reason it can't be used."""
    ref = parse_message_ref(text)
    if ref is None:
        return "That isn't a message link or ID. Right-click a message → **Copy Message Link**."
    channel_id, message_id = ref
    channel = interaction.guild.get_channel_or_thread(channel_id) if channel_id else interaction.channel
    if not isinstance(channel, discord.abc.Messageable):
        return "I can't find that channel in this server."
    member = interaction.user
    if isinstance(member, discord.Member) and not channel.permissions_for(member).read_message_history:
        return "You can't read that channel."
    try:
        return await channel.fetch_message(message_id)
    except discord.NotFound:
        return "I couldn't find that message. If it's in another channel, use its full link."
    except discord.Forbidden:
        return "I can't read that channel."


async def latest_message(channel, user: discord.abc.User | None) -> discord.Message | None:
    """The most recent message with text in this channel (from `user`, if given), skipping bots."""
    found = await latest_messages(channel, user, 1)
    return found[0] if found else None


async def latest_messages(channel, user: discord.abc.User | None, count: int) -> list[discord.Message]:
    """The last `count` messages with text (from `user`, if given), oldest first, skipping bots."""
    found = []
    async for message in channel.history(limit=50):
        if quotable(message) and (user is None or message.author.id == user.id):
            found.append(message)
            if len(found) == count:
                break
    return list(reversed(found))


async def messages_from(channel, first: discord.Message, count: int) -> list[discord.Message]:
    """`first` and the next messages after it, up to `count` with text, skipping bots."""
    found = [first] if quotable(first) else []
    async for message in channel.history(limit=50, after=first, oldest_first=True):
        if len(found) >= count:
            break
        if quotable(message):
            found.append(message)
    return found


def _allowed(interaction: discord.Interaction, requester_id: int, authors: set[int]) -> bool:
    member = interaction.user
    return member.id == requester_id or member.id in authors or (
        isinstance(member, discord.Member) and interaction.channel is not None
        and interaction.channel.permissions_for(member).manage_messages
    )


# ---- one message -----------------------------------------------------------------


def quote_view(quote_style: QuoteStyle, requester_id: int, channel_id: int, message_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in (*STYLE_ACTIONS, "delete"):
        view.add_item(QuoteButton(action, quote_style.bits, requester_id, channel_id, message_id))
    return view


class QuoteButton(discord.ui.DynamicItem[discord.ui.Button],
                  template=r"quote:(?P<action>colour|flip|light|tint|delete):(?P<bits>\d{1,2}):(?P<req>\d+):(?P<ch>\d+):(?P<msg>\d+)"):
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
        if not _allowed(interaction, self.requester_id, {original.author.id} if original else set()):
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
        image = await make_image(original, new_style, accent_for(interaction.client, original.guild))
        await interaction.edit_original_response(
            attachments=[image], view=quote_view(new_style, self.requester_id, self.channel_id, self.message_id)
        )


# ---- several messages ------------------------------------------------------------


def conversation_view(quote_style: QuoteStyle, requester_id: int, channel_id: int, first_id: int, count: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in (*CONVO_ACTIONS, "delete"):
        view.add_item(ConversationButton(action, quote_style.bits, requester_id, channel_id, first_id, count))
    return view


class ConversationButton(discord.ui.DynamicItem[discord.ui.Button],
                         template=r"quotes:(?P<action>colour|light|tint|delete):(?P<bits>\d{1,2}):(?P<req>\d+):(?P<ch>\d+):(?P<first>\d+):(?P<n>\d)"):
    def __init__(self, action: str, bits: int, requester_id: int, channel_id: int, first_id: int, count: int) -> None:
        label, emoji = STYLE_ACTIONS.get(action, ("Delete", "🗑️"))
        super().__init__(discord.ui.Button(
            label=label, emoji=emoji,
            style=discord.ButtonStyle.danger if action == "delete" else discord.ButtonStyle.secondary,
            custom_id=f"quotes:{action}:{bits}:{requester_id}:{channel_id}:{first_id}:{count}",
        ))
        self.action, self.bits, self.requester_id = action, bits, requester_id
        self.channel_id, self.first_id, self.count = channel_id, first_id, count

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> ConversationButton:
        return cls(match["action"], int(match["bits"]), int(match["req"]), int(match["ch"]), int(match["first"]), int(match["n"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.client.get_channel(self.channel_id)
        messages: list[discord.Message] = []
        if isinstance(channel, discord.abc.Messageable):
            try:
                messages = await messages_from(channel, await channel.fetch_message(self.first_id), self.count)
            except discord.HTTPException:
                messages = []
        if not _allowed(interaction, self.requester_id, {m.author.id for m in messages}):
            await interaction.response.send_message(
                embed=style.fail("Only the person who made this quote, or someone in it, can change it."), ephemeral=True)
            return
        if self.action == "delete":
            await interaction.response.defer()
            await interaction.message.delete()
            return
        if not messages:
            await interaction.response.send_message(embed=style.warn("Those messages are gone, so this quote can't change."),
                                                    ephemeral=True)
            return
        await interaction.response.defer()
        new_style = QuoteStyle.from_bits(self.bits).toggled(self.action)
        image = await make_conversation(messages, new_style, accent_for(interaction.client, messages[0].guild))
        await interaction.edit_original_response(
            attachments=[image],
            view=conversation_view(new_style, self.requester_id, self.channel_id, self.first_id, self.count),
        )


# ---- the cog ---------------------------------------------------------------------


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
        await self._send_quote(interaction, message)

    async def _send_quote(self, interaction: discord.Interaction, message: discord.Message) -> None:
        quote_style = await self.bot.quotes_db.default_style(interaction.guild_id)
        image = await make_image(message, quote_style, accent_for(self.bot, interaction.guild))
        await interaction.followup.send(file=image, view=quote_view(quote_style, interaction.user.id, message.channel.id, message.id))

    @app_commands.command(name="quote", description="Make a quote image from a message (default: the latest one here).")
    @app_commands.describe(
        message="A message link (right-click a message → Copy Message Link) or ID. Leave empty for the latest message.",
        user="Quote this person's latest message here instead",
        count="Put up to 5 messages on one image, like a conversation (from the link onwards, or the latest ones)",
    )
    @app_commands.guild_only()
    async def quote(self, interaction: discord.Interaction, message: str | None = None,
                    user: discord.Member | None = None, count: app_commands.Range[int, 1, MAX_MESSAGES] = 1) -> None:
        # Slash commands can't be sent as a reply, so messages are picked by link/ID, or the latest ones.
        await interaction.response.defer(thinking=True)
        if message:
            first = await find_message(interaction, message)
            if isinstance(first, str):
                await interaction.followup.send(embed=style.warn(first), ephemeral=True)
                return
            targets = await messages_from(first.channel, first, count) if count > 1 else [first]
        else:
            targets = await latest_messages(interaction.channel, user, count)
        if not targets:
            who = f"{user.mention}'s" if user else "any"
            await interaction.followup.send(embed=style.warn(f"I couldn't find {who} recent messages with text here."),
                                            ephemeral=True)
            return
        if problem := await self._problem(interaction.guild_id, targets[0], interaction.user.id):
            await interaction.followup.send(embed=style.warn(problem), ephemeral=True)
            return
        if len(targets) == 1:
            await self._send_quote(interaction, targets[0])
            return
        quote_style = await self.bot.quotes_db.default_style(interaction.guild_id)
        image = await make_conversation(targets, quote_style, accent_for(self.bot, interaction.guild))
        await interaction.followup.send(file=image, view=conversation_view(
            quote_style, interaction.user.id, targets[0].channel.id, targets[0].id, len(targets)))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Reply to a message and mention the bot. The mention has to be typed: Discord's automatic
        # reply ping (when replying to the bot's own messages) doesn't count.
        if message.guild is None or message.author.bot or message.reference is None or self.bot.user is None:
            return
        if f"<@{self.bot.user.id}>" not in message.content and f"<@!{self.bot.user.id}>" not in message.content:
            return
        if not await is_allowed(self.bot, message.author, message.guild):
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
        quote_style = await self.bot.quotes_db.default_style(message.guild.id)
        async with message.channel.typing():
            image = await make_image(target, quote_style, accent_for(self.bot, message.guild))
        await message.reply(file=image, view=quote_view(quote_style, message.author.id, target.channel.id, target.id),
                            mention_author=False)


def status_line(enabled: bool) -> str:
    return f"{style.ON} On · right-click a message → Apps → Make a quote image, or /quote" if enabled else f"{style.OFF} Off"
