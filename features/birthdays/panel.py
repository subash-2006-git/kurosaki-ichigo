"""Control panel section for birthdays."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from features.birthdays.cog import DEFAULT_MESSAGE, zone
from features.community.utils.checks import role_problem

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.birthdays_db.settings(guild.id)
    if not s.enabled:
        return f"{style.OFF} Off"
    channel = guild.get_channel(s.channel_id) if s.channel_id else None
    return f"{style.ON} On · {channel.mention if channel else 'no channel picked'} · {s.hour:02d}:00 {s.timezone}"


def parse_zone(text: str) -> str:
    name = text.strip()
    if name.upper() in ("UTC", "GMT"):
        return "UTC"
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"“{name}” isn't a time zone I know. Use a name like Asia/Kolkata, Europe/London or "
                         "America/New_York.") from None
    return name


class BirthdaysEditor(SectionEditor):
    async def build(self) -> None:
        s = await self.bot.birthdays_db.settings(self.guild.id)
        channel = self.guild.get_channel(s.channel_id) if s.channel_id else None
        role = self.guild.get_role(s.role_id) if s.role_id else None
        saved = len(await self.bot.birthdays_db.all(self.guild.id))
        local = datetime.now(zone(s.timezone)).strftime("%H:%M")
        role_line = f"**Birthday role** · {role.mention if role else 'none'}\n-# Given for the day, taken back the next day."
        if role and (problem := role_problem(self.guild, role)):
            role_line += f"\n{style.WARN} {problem}"
        self.add_item(self.container(
            self.text("## 🎂 Birthdays\nMembers save their birthday with `/birthday set` (day and month, no year). "
                      "On the day, the bot posts a message and can give a role."),
            discord.ui.Separator(),
            self.toggle("Birthdays", s.enabled, self.flip),
            self.text(f"**Announcement channel** · {channel.mention if channel else 'not set'}"),
            self.row(self.channel_select("Where birthdays are announced", s.channel_id, self.pick_channel)),
            self.text(role_line),
            self.row(self.role_select("Birthday role (optional)", [s.role_id] if s.role_id else [], self.pick_role)),
            self.setting("Time zone and hour", f"{s.hour:02d}:00 in {s.timezone} (it's {local} there now)", "Edit",
                         self.edit_time),
            self.setting("Message", (s.message or DEFAULT_MESSAGE)[:200], "Edit", self.edit_message),
            self.text(f"-# {saved} member(s) have saved a birthday here."),
        ))

    async def flip(self, interaction: discord.Interaction) -> None:
        s = await self.bot.birthdays_db.settings(self.guild.id)
        await self.bot.birthdays_db.update(self.guild.id, enabled=not s.enabled)
        await self.rerender(interaction)

    async def pick_channel(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.birthdays_db.update(self.guild.id, channel_id=int(values[0]) if values else None)
        await self.rerender(interaction)

    async def pick_role(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.birthdays_db.update(self.guild.id, role_id=int(values[0]) if values else None)
        await self.rerender(interaction)

    async def edit_time(self, interaction: discord.Interaction) -> None:
        s = await self.bot.birthdays_db.settings(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            name = parse_zone(values[0] or "UTC")
            try:
                hour = int(values[1] or "0")
            except ValueError:
                raise ValueError("The hour must be a whole number from 0 to 23.") from None
            if not 0 <= hour <= 23:
                raise ValueError("The hour must be from 0 to 23.")
            await self.bot.birthdays_db.update(self.guild.id, timezone=name, hour=hour)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Time zone and hour", [
            Field("Time zone", s.timezone, max_length=64, required=True, placeholder="Asia/Kolkata",
                  description="Decides when a day starts. Names like Asia/Kolkata or Europe/London."),
            Field("Hour (0–23)", str(s.hour), max_length=2, required=True,
                  description="When birthdays are posted, in that time zone. 0 = midnight."),
        ], save))

    async def edit_message(self, interaction: discord.Interaction) -> None:
        s = await self.bot.birthdays_db.settings(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            await self.bot.birthdays_db.update(self.guild.id, message=values[0] or None)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Birthday message", [
            Field("Message", s.message or DEFAULT_MESSAGE, paragraph=True, max_length=1000,
                  description="{users} = the birthday members, {server} = the server name. Empty = the default."),
        ], save))


SECTION = PanelSection(key="birthdays", label="Birthdays", emoji="🎂", order=26, status=status,
                       open=BirthdaysEditor.open)


async def check(bot: Bot, guild: discord.Guild) -> tuple[bool, str] | None:
    s = await bot.birthdays_db.settings(guild.id)
    if not s.enabled:
        return None
    return bool(s.channel_id and guild.get_channel(s.channel_id)), "Birthdays have a channel (🎂 Birthdays)"
