"""Control panel section for the starboard."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.starboard_db.settings(guild.id)
    if not s.enabled:
        return f"{style.OFF} Off"
    where = f"<#{s.channel_id}>" if s.channel_id and guild.get_channel(s.channel_id) else "no channel picked"
    return f"{style.ON} {s.threshold} × {s.emoji} → {where}"


def parse_emoji(text: str, guild: discord.Guild) -> str:
    """A unicode emoji, or one of this server's custom emoji (<:name:id> or :name:). Raises ValueError."""
    text = text.strip()
    if not text:
        raise ValueError("Enter an emoji, like ⭐.")
    partial = discord.PartialEmoji.from_str(text)
    if partial.id:
        if guild.get_emoji(partial.id) is None:
            raise ValueError("That custom emoji isn't from this server.")
        return str(partial)
    by_name = discord.utils.get(guild.emojis, name=text.strip(":"))
    if by_name is not None:
        return str(by_name)
    if len(text) > 16 or any(ch.isalnum() and ch.isascii() for ch in text):
        raise ValueError("Enter a single emoji, like ⭐, or one of this server's custom emoji.")
    return text


class StarboardEditor(SectionEditor):
    async def build(self) -> None:
        s = await self.bot.starboard_db.settings(self.guild.id)
        board = self.guild.get_channel(s.channel_id) if s.channel_id else None
        self.add_item(self.container(
            self.text("## ⭐ Starboard\nWhen a message gets enough reactions, it's reposted to a highlights channel "
                      "with a link back. It updates as stars change and leaves when they drop below the limit."),
            discord.ui.Separator(),
            self.toggle("Starboard", s.enabled, self.flip("enabled")),
            self.text(f"**Starboard channel** · {board.mention if board else 'not set'}"),
            self.row(self.channel_select("Where starred messages go", s.channel_id, self.pick_channel)),
            self.setting("Reaction and stars needed", f"{s.emoji} × {s.threshold}", "Edit", self.edit_rule),
            self.toggle("Count people starring their own message", s.allow_self, self.flip("allow_self")),
        ))

    def flip(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            s = await self.bot.starboard_db.settings(self.guild.id)
            await self.bot.starboard_db.update(self.guild.id, **{field: not getattr(s, field)})
            await self.rerender(interaction)
        return callback

    async def pick_channel(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.starboard_db.update(self.guild.id, channel_id=int(values[0]) if values else None)
        await self.rerender(interaction)

    async def edit_rule(self, interaction: discord.Interaction) -> None:
        s = await self.bot.starboard_db.settings(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            emoji = parse_emoji(values[0], self.guild)
            try:
                threshold = int(values[1])
            except ValueError:
                raise ValueError("Stars needed must be a whole number.") from None
            if not 1 <= threshold <= 100:
                raise ValueError("Stars needed must be between 1 and 100.")
            await self.bot.starboard_db.update(self.guild.id, emoji=emoji, threshold=threshold)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Starboard rule", [
            Field("Reaction", s.emoji, max_length=100, required=True, description="An emoji, or one of this server's custom emoji."),
            Field("Stars needed", str(s.threshold), max_length=3, required=True, description="1–100"),
        ], save))


SECTION = PanelSection(key="starboard", label="Starboard", emoji="⭐", order=27, status=status, open=StarboardEditor.open)
