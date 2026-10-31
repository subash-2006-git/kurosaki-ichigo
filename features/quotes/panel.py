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
        self.add_item(self.container(
            self.text("## 💬 Quote images\nTurn any message into a picture: the author's avatar fading into their words.\n"
                      "-# Right-click a message → **Apps → Make a quote image**, or reply to it and mention me. "
                      "Buttons under the image switch colour, side and light/dark."),
            discord.ui.Separator(),
            self.toggle("Quote images", enabled, self.flip),
        ))

    async def flip(self, interaction: discord.Interaction) -> None:
        await self.bot.quotes_db.set_enabled(self.guild.id, not await self.bot.quotes_db.enabled(self.guild.id))
        await self.rerender(interaction)


SECTION = PanelSection(key="quotes", label="Quote images", emoji="💬", order=28, status=status, open=QuotesEditor.open)
