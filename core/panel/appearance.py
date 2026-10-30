"""Panel section: the server's theme colour and footer, used by every bot embed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.ui import style
from core.ui.embed_draft import parse_colour

if TYPE_CHECKING:
    from core.bot import Bot

PRESETS: list[tuple[int, str, str]] = [
    (0x5865F2, "Blurple", "🟦"),
    (0xED4245, "Red", "🟥"),
    (0xF97316, "Orange", "🟧"),
    (0xF1C40F, "Gold", "🟨"),
    (0x57F287, "Green", "🟩"),
    (0x1ABC9C, "Teal", "🌊"),
    (0x3498DB, "Blue", "🔵"),
    (0x9B59B6, "Purple", "🟪"),
    (0xEB459E, "Pink", "🌸"),
    (0x95A5A6, "Grey", "⬜"),
    (0x2B2D31, "Dark", "⬛"),
]


async def status(bot: Bot, guild: discord.Guild) -> str:
    theme = bot.theme.get(guild)
    footer = f"footer “{theme.footer_text}”" if theme.footer_text else "no footer"
    return f"Theme colour `{style.hex_colour(theme.accent)}` · {footer}"


class AppearanceEditor(SectionEditor):
    async def build(self) -> None:
        theme = self.bot.theme.get(self.guild)
        current = f"{theme.accent:06X}"
        preset_names = {f"{value:06X}": name for value, name, _ in PRESETS}
        shown = preset_names.get(current, "custom")
        self.add_item(self.container(
            self.text(f"## {style.PALETTE} Appearance\n"
                      "Your theme colour is used by the bot's messages in this server. "
                      "The bar on the left of this editor already shows it."),
            discord.ui.Separator(),
            self.setting("Theme colour", f"`#{current}` · {shown}", "Custom…", self.custom_colour),
            self.row(self.choice_select(
                "Pick a theme colour",
                [(f"{value:06X}", f"{name}  #{value:06X}") for value, name, _ in PRESETS],
                current,
                self.pick_preset,
                emojis={f"{value:06X}": emoji for value, _, emoji in PRESETS},
            )),
            self.setting(
                "Footer text",
                f"“{theme.footer_text}”" if theme.footer_text else "None. Shown at the bottom of bot messages when set.",
                "Edit", self.edit_footer,
            ),
            discord.ui.Separator(),
            self.row(
                self.button("Preview a message", self.preview, emoji="👀", style_=discord.ButtonStyle.primary),
                self.button("Reset to default", self.reset, style_=discord.ButtonStyle.danger),
            ),
        ))

    async def pick_preset(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        await self.bot.theme.update(self.guild.id, accent=int(value, 16))
        await self.rerender(interaction)

    async def custom_colour(self, interaction: discord.Interaction) -> None:
        theme = self.bot.theme.get(self.guild)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            colour = parse_colour(values[0])  # DraftError is a ValueError: the form shows it
            if colour is None:
                raise ValueError("Enter a colour, or use **Reset to default**.")
            await self.bot.theme.update(self.guild.id, accent=colour)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Theme colour", [
            Field("Colour", style.hex_colour(theme.accent), placeholder="#5865F2, or a name like gold or teal",
                  description="A hex code or a colour name.", max_length=20, required=True),
        ], save))

    async def edit_footer(self, interaction: discord.Interaction) -> None:
        theme = self.bot.theme.get(self.guild)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            await self.bot.theme.update(self.guild.id, footer_text=values[0] or None)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Footer text", [
            Field("Footer", theme.footer_text, placeholder=f"e.g. {self.guild.name}",
                  description="Shown at the bottom of bot messages. Leave empty for none.", max_length=200),
        ], save))

    async def reset(self, interaction: discord.Interaction) -> None:
        await self.bot.theme.update(self.guild.id, accent=None, footer_text=None)
        await self.rerender(interaction)

    async def preview(self, interaction: discord.Interaction) -> None:
        embed = style.info(
            f"{style.PALETTE} This is how bot messages look",
            "Neutral messages, like settings, ranks and log entries, use your theme colour. "
            "Success, warning and error messages keep their green, amber and red.",
        )
        embed.add_field(name="Theme colour", value=f"`{style.hex_colour(self.bot.theme.get(self.guild).accent)}`")
        embed.add_field(name="Example", value=f"{style.OK} Saved")
        self.bot.theme.brand(embed, self.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)
