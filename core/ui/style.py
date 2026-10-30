"""The bot's shared look: colours, emoji and small embed helpers used by every feature.

Neutral embeds are built with no colour; ThemeStore.brand() then gives them the server's theme
colour. Success/error/warning colours are semantic and stay the same in every server.
"""

from __future__ import annotations

import discord

DEFAULT_ACCENT = 0x5865F2  # used until a server picks its own theme colour

SUCCESS = discord.Colour(0x57F287)
ERROR = discord.Colour(0xED4245)
WARNING = discord.Colour(0xF0B232)

# Colours older code uses for "nothing in particular"; brand() treats them like no colour.
NEUTRAL_COLOURS = frozenset({0x5865F2, 0x7289DA, 0x99AAB5, 0x95A5A6, 0x2F3136, 0x607D8B})

ON = "🟢"
OFF = "⚪"
OK = "✅"
FAIL = "❌"
WARN = "⚠️"
GEAR = "⚙️"
PALETTE = "🎨"


def ok(text: str) -> discord.Embed:
    return discord.Embed(description=f"{OK} {text}", colour=SUCCESS)


def fail(text: str) -> discord.Embed:
    return discord.Embed(description=f"{FAIL} {text}", colour=ERROR)


def warn(text: str) -> discord.Embed:
    return discord.Embed(description=f"{WARN} {text}", colour=WARNING)


def info(title: str | None = None, text: str | None = None) -> discord.Embed:
    """A neutral embed; brand() gives it the server's theme colour."""
    return discord.Embed(title=title, description=text)


def on_off(value: bool) -> str:
    return f"{ON} On" if value else f"{OFF} Off"


def hex_colour(value: int) -> str:
    return f"#{value:06X}"
