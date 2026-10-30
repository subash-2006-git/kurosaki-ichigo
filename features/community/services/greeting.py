"""Builds welcome and goodbye messages: text plus a card (or the plain background image)."""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp
import discord

from features.community.services.cards import render_greeting_card
from features.community.services.guild_config import GuildConfig
from features.community.services.media import Kind, MediaStore, inspect_image
from features.community.utils.placeholders import member_count, render

log = logging.getLogger(__name__)

MAX_CONTENT = 2000


@dataclass
class GreetingMessage:
    content: str
    file: discord.File | None

    def send_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"content": self.content}
        if self.file is not None:
            kwargs["file"] = self.file
        return kwargs


@dataclass(frozen=True)
class GreetingSettings:
    enabled: bool
    channel_id: int | None
    text: str
    background_source: str | None
    card_enabled: bool


def greeting_settings(config: GuildConfig, kind: Kind) -> GreetingSettings:
    """The welcome_* or goodbye_* settings, without the prefix."""
    return GreetingSettings(
        enabled=getattr(config, f"{kind}_enabled"),
        channel_id=getattr(config, f"{kind}_channel_id"),
        text=getattr(config, f"{kind}_text"),
        background_source=getattr(config, f"{kind}_gif_url"),
        card_enabled=getattr(config, f"{kind}_card_enabled"),
    )


def card_subtitle(member: discord.Member, kind: Kind) -> str:
    count = member_count(member.guild)
    if kind == "welcome":
        return f"Member #{count:,} of {member.guild.name}"
    return f"{member.guild.name} now has {count:,} members"


async def avatar_bytes(member: discord.Member) -> bytes | None:
    try:
        return await member.display_avatar.replace(size=256, format="png").read()
    except discord.DiscordException:
        log.warning("Could not download avatar for user %s in guild %s", member.id, member.guild.id)
        return None


async def render_card(member: discord.Member, kind: Kind, background: bytes | None) -> discord.File | None:
    """Render the card, or return None (and log) so the message can go out without it."""
    avatar = await avatar_bytes(member)
    try:
        card = await asyncio.to_thread(
            render_greeting_card,
            kind,
            avatar,
            kind.upper(),
            member.display_name,
            card_subtitle(member, kind),
            background,
        )
    except Exception:
        log.warning("Failed to render %s card in guild %s", kind, member.guild.id, exc_info=True)
        return None
    return discord.File(io.BytesIO(card.data), filename=f"{kind}.{card.extension}")


async def _plain_background(kind: Kind, background: bytes) -> discord.File | None:
    try:
        info = await asyncio.to_thread(inspect_image, background)
    except Exception:
        return None
    extension = {"JPEG": "jpg"}.get(info.format, info.format.lower())
    return discord.File(io.BytesIO(background), filename=f"{kind}.{extension}")


async def build_greeting(
    member: discord.Member,
    kind: Kind,
    config: GuildConfig,
    media: MediaStore,
    session: aiohttp.ClientSession | None,
) -> GreetingMessage:
    settings = greeting_settings(config, kind)
    content = render(settings.text, member)[:MAX_CONTENT]
    background = await media.get_background(session, member.guild.id, kind, settings.background_source)

    if settings.card_enabled:
        file = await render_card(member, kind, background)
    elif background is not None:
        file = await _plain_background(kind, background)
    else:
        file = None
    return GreetingMessage(content=content, file=file)


async def send_goodbye_dm(member: discord.Member, config: GuildConfig) -> bool:
    """Best effort: Discord only lets bots DM people they still share a server with."""
    try:
        await member.send(render(config.goodbye_dm_text, member)[:MAX_CONTENT])
    except discord.HTTPException as exc:
        log.info("Goodbye DM to %s (guild %s) not delivered: %s", member.id, member.guild.id, exc)
        return False
    log.info("Sent goodbye DM to %s (guild %s)", member.id, member.guild.id)
    return True
