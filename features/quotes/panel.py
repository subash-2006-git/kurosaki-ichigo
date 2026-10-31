"""Control panel section for quote images: just an on/off switch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import SectionEditor
from core.panel.service import PanelSection
from features.quotes.cog import status_line

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    return status_line(await bot.quotes_db.enabled(guild.id))


class QuotesEditor(SectionEditor):
    async def build(self) -> None:
        enabled = await self.bot.quotes_db.enabled(self.guild.id)
        default = await self.bot.quotes_db.default_style(self.guild.id)
        self.add_item(self.container(
            self.text("## 💬 Quote images\nTurn any message into a picture: the author's avatar fading into their words.\n"
                      "-# Right-click a message → **Apps → Make a quote image**, reply to it and mention me, or use "
                      "`/quote` (add `count:` for up to 5 messages as a conversation). Buttons under each image change its look."),
            discord.ui.Separator(),
            self.toggle("Quote images", enabled, self.flip),
            self.text("**Default look**\n-# How new quotes start out. People can still change each one with its buttons."),
            self.toggle("Avatar in colour", default.colour, self._style("colour"), description="Off = black and white."),
            self.toggle("Avatar on the right", default.flip, self._style("flip")),
            self.toggle("Light background", default.light, self._style("light")),
            self.toggle("Tint with the server colour", default.tint, self._style("tint"),
                        description="Uses the theme colour from 🎨 Appearance."),
        ))

    async def flip(self, interaction: discord.Interaction) -> None:
        await self.bot.quotes_db.set_enabled(self.guild.id, not await self.bot.quotes_db.enabled(self.guild.id))
        await self.rerender(interaction)

    def _style(self, option: str):
        async def callback(interaction: discord.Interaction) -> None:
            current = await self.bot.quotes_db.default_style(self.guild.id)
            await self.bot.quotes_db.set_default_style(self.guild.id, current.toggled(option))
            await self.rerender(interaction)
        return callback


SECTION = PanelSection(key="quotes", label="Quote images", emoji="💬", order=28, status=status, open=QuotesEditor.open)
