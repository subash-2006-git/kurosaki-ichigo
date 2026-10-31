"""/birthday set, remove and upcoming, and the daily announcement with the birthday role.

Each enabled server is checked every few minutes against its own time zone. Once the chosen hour
has passed on a new local day, that day is claimed in the database (so it runs once, even across
restarts), yesterday's birthday role is taken back, and today's birthdays are announced.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.ui import style
from core.ui.theme import brand
from features.birthdays.db import is_leap
from features.community.utils.checks import role_problem

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
DEFAULT_MESSAGE = "Happy birthday {users}! 🎉"
UPCOMING = 10
REASON = "Birthday role"


# ---- dates and text (pure, tested) -------------------------------------------------------


def valid_day(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= MONTH_DAYS[month - 1]


def format_day(month: int, day: int) -> str:
    return f"{day} {MONTHS[month - 1]}"


def next_occurrence(month: int, day: int, today: date) -> date:
    """The next date (today included) this birthday is celebrated; 29 February is the 28th in other years."""
    for year in (today.year, today.year + 1):
        celebrated = date(year, 2, 28) if (month, day) == (2, 29) and not is_leap(year) else date(year, month, day)
        if celebrated >= today:
            return celebrated
    raise AssertionError("unreachable")


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def fill(template: str | None, mentions: list[str], server: str) -> str:
    users = ", ".join(mentions[:-1]) + f" and {mentions[-1]}" if len(mentions) > 1 else (mentions or [""])[0]
    return (template or DEFAULT_MESSAGE).replace("{users}", users).replace("{server}", server)


def due(settings, now: datetime) -> bool:
    """Whether this server's birthdays should be handled now (a new local day, past the chosen hour)."""
    return settings.last_run != now.date() and now.hour >= settings.hour


# ---- the cog -----------------------------------------------------------------------------


class Birthdays(commands.Cog):
    birthday = app_commands.Group(name="birthday", description="Your birthday in this server", guild_only=True)

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.daily.start()

    async def cog_unload(self) -> None:
        self.daily.cancel()

    @birthday.command(name="set", description="Save your birthday (day and month only) so the server can celebrate it")
    @app_commands.choices(month=[app_commands.Choice(name=name, value=i) for i, name in enumerate(MONTHS, start=1)])
    async def set_birthday(self, interaction: discord.Interaction, month: app_commands.Choice[int],
                           day: app_commands.Range[int, 1, 31]) -> None:
        if not valid_day(month.value, day):
            await interaction.response.send_message(embed=style.fail(f"{month.name} doesn't have {day} days."),
                                                    ephemeral=True)
            return
        await self.bot.birthdays_db.set(interaction.guild_id, interaction.user.id, month.value, day)
        settings = await self.bot.birthdays_db.settings(interaction.guild_id)
        text = f"Saved: **{format_day(month.value, day)}**. Members can see it with `/birthday upcoming`."
        if not settings.enabled:
            text += "\n-# Birthdays aren't announced in this server right now."
        await interaction.response.send_message(embed=style.ok(text), ephemeral=True)

    @birthday.command(name="remove", description="Remove your birthday from this server")
    async def remove_birthday(self, interaction: discord.Interaction) -> None:
        removed = await self.bot.birthdays_db.remove(interaction.guild_id, interaction.user.id)
        embed = style.ok("Your birthday is removed.") if removed else style.warn("You haven't saved a birthday here.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @birthday.command(name="upcoming", description="The next birthdays in this server")
    async def upcoming(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        settings = await self.bot.birthdays_db.settings(guild.id)
        today = datetime.now(zone(settings.timezone)).date()
        rows = [r for r in await self.bot.birthdays_db.all(guild.id) if guild.get_member(r.user_id)]
        rows.sort(key=lambda r: next_occurrence(r.month, r.day, today))
        lines = []
        for r in rows[:UPCOMING]:
            when = next_occurrence(r.month, r.day, today)
            soon = "today 🎂" if when == today else discord.utils.format_dt(
                datetime(when.year, when.month, when.day, tzinfo=zone(settings.timezone)), "R")
            lines.append(f"**{format_day(r.month, r.day)}** · <@{r.user_id}> · {soon}")
        embed = style.info("🎂 Upcoming birthdays", "\n".join(lines) or "Nobody has saved a birthday yet. "
                                                                     "Use `/birthday set` to add yours.")
        await interaction.response.send_message(embed=brand(embed, guild), allowed_mentions=discord.AllowedMentions.none())

    # --- the daily run ------------------------------------------------------------

    @tasks.loop(minutes=10)
    async def daily(self) -> None:
        try:
            servers = await self.bot.birthdays_db.enabled_settings()
        except Exception:
            log.exception("Loading birthday settings failed")
            return
        for settings in servers:
            guild = self.bot.get_guild(settings.guild_id)
            now = datetime.now(zone(settings.timezone))
            if guild is None or not due(settings, now):
                continue
            try:
                if await self.bot.birthdays_db.claim_day(guild.id, now.date()):
                    await self.celebrate(guild, settings, now.date())
            except Exception:
                log.exception("Birthdays failed in guild %s", guild.id)

    @daily.before_loop
    async def before_daily(self) -> None:
        await self.bot.wait_until_ready()

    async def celebrate(self, guild: discord.Guild, settings, today: date) -> None:
        user_ids = await self.bot.birthdays_db.on(guild.id, today)
        members = [m for m in (guild.get_member(u) for u in user_ids) if m is not None and not m.bot]
        await self._move_role(guild, settings, members)
        channel = guild.get_channel(settings.channel_id) if settings.channel_id else None
        if not members or not isinstance(channel, discord.abc.Messageable):
            return
        mentions = [m.mention for m in members]
        embed = brand(style.info("🎂 Happy birthday!", fill(settings.message, mentions, guild.name)), guild)
        if len(members) == 1:
            embed.set_thumbnail(url=members[0].display_avatar.url)
        try:
            await channel.send(" ".join(mentions), embed=embed, allowed_mentions=discord.AllowedMentions(users=True))
        except discord.HTTPException as exc:
            log.warning("Birthday message not sent in guild %s: %s", guild.id, exc)

    async def _move_role(self, guild: discord.Guild, settings, members: list[discord.Member]) -> None:
        """Take the birthday role from yesterday's members and give it to today's."""
        role = guild.get_role(settings.role_id) if settings.role_id else None
        if role is None:
            return
        if problem := role_problem(guild, role):
            log.warning("Can't hand out the birthday role in guild %s: %s", guild.id, problem)
            return
        for member in [m for m in role.members if m not in members]:
            try:
                await member.remove_roles(role, reason=REASON)
            except discord.HTTPException as exc:
                log.warning("Birthday role not removed from %s: %s", member.id, exc)
        for member in [m for m in members if role not in m.roles]:
            try:
                await member.add_roles(role, reason=REASON)
            except discord.HTTPException as exc:
                log.warning("Birthday role not given to %s: %s", member.id, exc)
