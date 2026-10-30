"""Each server's theme (accent colour, footer), kept in memory so embeds can be styled without a query.

Every row of core.guild_theme is loaded at startup; changes write through to the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg
import discord

from core.ui.style import DEFAULT_ACCENT, NEUTRAL_COLOURS

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Theme:
    accent: int = DEFAULT_ACCENT
    footer_text: str | None = None

    @property
    def colour(self) -> discord.Colour:
        return discord.Colour(self.accent)


def _guild_id(guild: discord.abc.Snowflake | int | None) -> int | None:
    if guild is None:
        return None
    return guild if isinstance(guild, int) else guild.id


class ThemeStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._themes: dict[int, Theme] = {}

    async def load(self) -> None:
        rows = await self.pool.fetch("SELECT guild_id, accent, footer_text FROM core.guild_theme")
        self._themes = {
            r["guild_id"]: Theme(accent=r["accent"] if r["accent"] is not None else DEFAULT_ACCENT, footer_text=r["footer_text"])
            for r in rows
        }
        log.info("Loaded %d server theme(s)", len(self._themes))

    def get(self, guild: discord.abc.Snowflake | int | None) -> Theme:
        guild_id = _guild_id(guild)
        return self._themes.get(guild_id, Theme()) if guild_id is not None else Theme()

    async def update(self, guild_id: int, *, accent: int | None = ..., footer_text: str | None = ...) -> Theme:
        """Change the accent and/or footer (pass None to reset one to the default)."""
        current = self.get(guild_id)
        new_accent = current.accent if accent is ... else (accent if accent is not None else DEFAULT_ACCENT)
        new_footer = current.footer_text if footer_text is ... else ((footer_text or "").strip() or None)
        await self.pool.execute(
            "INSERT INTO core.guild_theme (guild_id, accent, footer_text) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id) DO UPDATE SET accent = EXCLUDED.accent, footer_text = EXCLUDED.footer_text, "
            "updated_at = NOW()",
            guild_id,
            new_accent,
            new_footer,
        )
        theme = Theme(accent=new_accent, footer_text=new_footer)
        self._themes[guild_id] = theme
        return theme

    def brand(self, embed: discord.Embed, guild: discord.abc.Snowflake | int | None) -> discord.Embed:
        """Give a neutral embed the server's theme colour, and add the theme footer if it has none."""
        theme = self.get(guild)
        if embed.colour is None or embed.colour.value in NEUTRAL_COLOURS:
            embed.colour = theme.colour
        if theme.footer_text and not (embed.footer and embed.footer.text):
            embed.set_footer(text=theme.footer_text)
        return embed


# The running bot's store, so helpers that only know the guild can theme an embed.
_active: ThemeStore | None = None


def activate(store: ThemeStore) -> None:
    global _active
    _active = store


def brand(embed: discord.Embed, guild: discord.abc.Snowflake | int | None) -> discord.Embed:
    """ThemeStore.brand() on the running bot's store; leaves the embed alone before startup (tests)."""
    return _active.brand(embed, guild) if _active is not None else embed
