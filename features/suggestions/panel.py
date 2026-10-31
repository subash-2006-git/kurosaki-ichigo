"""Control panel section for suggestions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import SectionEditor
from core.panel.service import PanelSection
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.suggestions_db.settings(guild.id)
    if not s.enabled:
        return f"{style.OFF} Off"
    channel = guild.get_channel(s.channel_id) if s.channel_id else None
    return f"{style.ON} On · {channel.mention if channel else 'no channel picked'}"


class SuggestionsEditor(SectionEditor):
    async def build(self) -> None:
        s = await self.bot.suggestions_db.settings(self.guild.id)
        channel = self.guild.get_channel(s.channel_id) if s.channel_id else None
        counts = await self.bot.suggestions_db.counts(self.guild.id)
        self.add_item(self.container(
            self.text("## 💡 Suggestions\nMembers post ideas with `/suggest`. Everyone votes 👍 or 👎 with the buttons, "
                      "and staff (**Manage Messages**) accept or deny them, with an optional reason. "
                      "The member gets a DM with the decision."),
            discord.ui.Separator(),
            self.toggle("Suggestions", s.enabled, self.flip("enabled"), description="When off, `/suggest` says it's off."),
            self.text(f"**Suggestions channel** · {channel.mention if channel else 'not set'}"),
            self.row(self.channel_select("Where suggestions are posted", s.channel_id, self.pick_channel,
                                         types=[discord.ChannelType.text])),
            self.toggle("Discussion thread on each suggestion", s.threads, self.flip("threads")),
            self.text(f"-# So far: {counts['open']} open · {counts['accepted']} accepted · {counts['denied']} denied"),
        ))

    def flip(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            s = await self.bot.suggestions_db.settings(self.guild.id)
            await self.bot.suggestions_db.update(self.guild.id, **{field: not getattr(s, field)})
            await self.rerender(interaction)
        return callback

    async def pick_channel(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.suggestions_db.update(self.guild.id, channel_id=int(values[0]) if values else None)
        await self.rerender(interaction)


SECTION = PanelSection(key="suggestions", label="Suggestions", emoji="💡", order=29, status=status,
                       open=SuggestionsEditor.open)


async def check(bot: Bot, guild: discord.Guild) -> tuple[bool, str] | None:
    s = await bot.suggestions_db.settings(guild.id)
    if not s.enabled:
        return None
    return bool(s.channel_id and guild.get_channel(s.channel_id)), "Suggestions have a channel (💡 Suggestions)"
