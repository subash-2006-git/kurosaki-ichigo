"""Starboard: count the star reaction on a message and keep its post on the board in step."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Literal

import discord
from discord.ext import commands

from core.ui.theme import brand
from features.logging.services.log_router import link_view

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

Decision = Literal["post", "edit", "delete", "none"]


def decide(stars: int, threshold: int, on_board: bool, previous: int | None = None) -> Decision:
    """What to do with the board post for a message that now has `stars`."""
    if stars >= threshold:
        if not on_board:
            return "post"
        return "edit" if stars != previous else "none"
    return "delete" if on_board else "none"


def first_image(message: discord.Message) -> str | None:
    for attachment in message.attachments:
        if (attachment.content_type or "").startswith("image/"):
            return attachment.url
    for embed in message.embeds:
        if embed.image and embed.image.url:
            return embed.image.url
        if embed.type in ("image", "gifv") and embed.thumbnail and embed.thumbnail.url:
            return embed.thumbnail.url
    return None


def board_post(message: discord.Message, stars: int, emoji: str) -> dict:
    embed = discord.Embed(description=message.content[:4000] or None, timestamp=message.created_at)
    embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
    if image := first_image(message):
        embed.set_image(url=image)
    elif message.attachments:
        embed.add_field(name="Attachment", value=f"[{message.attachments[0].filename}]({message.attachments[0].url})")
    brand(embed, message.guild)
    return {
        "content": f"{emoji} **{stars}** · {message.channel.mention}",
        "embed": embed,
        "view": link_view([("Jump to message", message.jump_url)]),
        "allowed_mentions": discord.AllowedMentions.none(),
    }


class Starboard(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._changed(payload.guild_id, payload.channel_id, payload.message_id, str(payload.emoji))

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._changed(payload.guild_id, payload.channel_id, payload.message_id, str(payload.emoji))

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        await self._changed(payload.guild_id, payload.channel_id, payload.message_id, None)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        await self._changed(payload.guild_id, payload.channel_id, payload.message_id, str(payload.emoji))

    async def _changed(self, guild_id: int | None, channel_id: int, message_id: int, emoji: str | None) -> None:
        if guild_id is None:
            return
        settings = await self.bot.starboard_db.settings(guild_id)
        if not settings.enabled or not settings.channel_id or channel_id == settings.channel_id:
            return
        if emoji is not None and emoji != settings.emoji:
            return
        async with self._locks[message_id]:
            try:
                await self.refresh(guild_id, channel_id, message_id, settings)
            except discord.HTTPException as exc:
                log.warning("Starboard update failed for message %s: %s", message_id, exc)
        self._locks.pop(message_id, None)

    async def count(self, message: discord.Message, settings) -> int:
        reaction = next((r for r in message.reactions if str(r.emoji) == settings.emoji), None)
        if reaction is None:
            return 0
        stars = reaction.count
        if not settings.allow_self:
            async for user in reaction.users():
                if user.id == message.author.id:
                    stars -= 1
                    break
        return stars

    async def refresh(self, guild_id: int, channel_id: int, message_id: int, settings) -> None:
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel_or_thread(channel_id) if guild else None
        board = guild.get_channel(settings.channel_id) if guild else None
        if channel is None or not isinstance(board, discord.TextChannel):
            return
        entry = await self.bot.starboard_db.entry(message_id)
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            message = None
        if message is None or message.author.bot or (getattr(channel, "nsfw", False) and not board.nsfw):
            stars = 0
        else:
            stars = await self.count(message, settings)

        action = decide(stars, settings.threshold, entry is not None, entry.stars if entry else None)
        if action == "post":
            post = await board.send(**board_post(message, stars, settings.emoji))
            await self.bot.starboard_db.save_entry(message.id, guild_id, channel_id, message.author.id, post.id, stars)
        elif action == "edit":
            try:
                await board.get_partial_message(entry.star_message_id).edit(content=board_post(message, stars, settings.emoji)["content"])
            except discord.NotFound:  # someone deleted the board post: post it again
                post = await board.send(**board_post(message, stars, settings.emoji))
                entry.star_message_id = post.id
            await self.bot.starboard_db.save_entry(message.id, guild_id, channel_id, message.author.id,
                                                   entry.star_message_id, stars)
        elif action == "delete":
            try:
                await board.get_partial_message(entry.star_message_id).delete()
            except discord.NotFound:
                pass
            await self.bot.starboard_db.delete_entry(message_id)
