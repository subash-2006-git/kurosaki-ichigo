"""Moderation's embeds, built on the bot's shared style (core.ui.style) and the server theme."""

import discord

from core.ui import style
from core.ui.theme import brand

SUCCESS = style.SUCCESS
ERROR = style.ERROR
INFO = None  # neutral: themed by brand()

ACTION_COLORS = {
    "warn": discord.Color(0xF0B232),
    "timeout": discord.Color.orange(),
    "untimeout": discord.Color.teal(),
    "kick": discord.Color.dark_orange(),
    "softban": discord.Color.dark_orange(),
    "ban": discord.Color.red(),
    "tempban": discord.Color.red(),
    "unban": discord.Color.green(),
}

ACTION_ICONS = {
    "warn": "⚠️",
    "timeout": "⏳",
    "untimeout": "✅",
    "kick": "👢",
    "softban": "🧹",
    "ban": "🔨",
    "tempban": "⏱️",
    "unban": "🕊️",
}

ACTION_LABELS = {
    "warn": "Warning",
    "timeout": "Timeout",
    "untimeout": "Timeout removed",
    "kick": "Kick",
    "softban": "Softban",
    "ban": "Ban",
    "tempban": "Temporary ban",
    "unban": "Unban",
}

ACTION_PAST_TENSE = {
    "warn": "warned",
    "timeout": "timed out",
    "untimeout": "had your timeout removed",
    "kick": "kicked",
    "softban": "kicked",
    "ban": "banned",
    "tempban": "temporarily banned",
    "unban": "unbanned",
}


def success(message: str) -> discord.Embed:
    return style.ok(message)


def error(message: str) -> discord.Embed:
    return style.fail(message)


def info(title: str, description: str | None = None, guild: discord.abc.Snowflake | None = None) -> discord.Embed:
    """A neutral embed in the server's theme colour (when `guild` is given)."""
    return brand(style.info(title, description), guild)


def truncate(text: str | None, limit: int = 1024) -> str:
    if not text:
        return "No reason provided"
    return text if len(text) <= limit else text[: limit - 1] + "…"
