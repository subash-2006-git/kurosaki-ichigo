"""/remind me, /remind list and /remind cancel, and the loop that delivers due reminders."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.ui import style
from core.ui.theme import brand
from features.moderation.utils.time import humanize, parse_duration
from features.reminders.db import MAX_PER_USER

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

MIN_DELAY = timedelta(minutes=1)
MAX_DELAY = timedelta(days=365)
MAX_TEXT = 500


def parse_delay(text: str) -> timedelta:
    """'2h', '1d12h', '30m'… between a minute and a year. Raises ValueError with a friendly message."""
    delay = parse_duration(text)
    if delay is None:
        raise ValueError("Use a time like `10m`, `2h`, `1d12h` or `3w`.")
    if delay < MIN_DELAY:
        raise ValueError("Reminders need to be at least a minute away.")
    if delay > MAX_DELAY:
        raise ValueError("Reminders can be at most a year away.")
    return delay


def reminder_embed(reminder, guild: discord.Guild | None) -> discord.Embed:
    embed = style.info("⏰ Reminder", reminder.text)
    set_at = discord.utils.format_dt(reminder.created_at, "R")
    embed.add_field(name="Set", value=f"{set_at}" + (f" · [jump to it]({reminder.jump_url})" if reminder.jump_url else ""))
    return brand(embed, guild)


def snippet(reminder) -> str:
    return reminder.text if len(reminder.text) <= 60 else reminder.text[:59] + "…"


def short(reminder) -> str:
    return f"#{reminder.id} · {snippet(reminder)}"


class Reminders(commands.Cog):
    remind = app_commands.Group(name="remind", description="Reminders")

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.deliver.start()

    async def cog_unload(self) -> None:
        self.deliver.cancel()

    @remind.command(name="me", description="Get pinged about something later")
    @app_commands.describe(when="How long from now, like 10m, 2h, 1d12h or 3w", about="What to remind you about",
                           private="Send it to your DMs instead of this channel")
    async def me(self, interaction: discord.Interaction, when: str, about: app_commands.Range[str, 1, MAX_TEXT],
                 private: bool = False) -> None:
        try:
            delay = parse_delay(when)
        except ValueError as exc:
            await interaction.response.send_message(embed=style.fail(str(exc)), ephemeral=True)
            return
        private = private or interaction.guild is None
        due_at = discord.utils.utcnow() + delay
        reminder = await self.bot.reminders_db.add(interaction.user.id, interaction.guild_id, interaction.channel_id,
                                                   about.strip(), private, due_at)
        if reminder is None:
            await interaction.response.send_message(
                embed=style.warn(f"You already have {MAX_PER_USER} reminders. Cancel one with `/remind cancel` first."),
                ephemeral=True)
            return
        where = "in your DMs" if private else "here"
        text = (f"I'll remind you {discord.utils.format_dt(due_at, 'R')} ({humanize(delay.total_seconds())}) {where}: "
                f"**{discord.utils.escape_markdown(reminder.text)[:200]}**")
        await interaction.response.send_message(embed=brand(style.ok(text), interaction.guild), ephemeral=private,
                                                allowed_mentions=discord.AllowedMentions.none())
        if not private:
            try:
                message = await interaction.original_response()
                await self.bot.reminders_db.set_jump_url(reminder.id, message.jump_url)
            except discord.HTTPException:
                pass

    @remind.command(name="list", description="Your upcoming reminders")
    async def list_reminders(self, interaction: discord.Interaction) -> None:
        reminders = await self.bot.reminders_db.for_user(interaction.user.id)
        lines = [f"**#{r.id}** · {discord.utils.format_dt(r.due_at, 'R')} · {discord.utils.escape_markdown(snippet(r))}"
                 + (" · 🔒 DM" if r.private else "") for r in reminders]
        embed = style.info("⏰ Your reminders", "\n".join(lines) or "None. Set one with `/remind me`.")
        embed.set_footer(text=f"{len(reminders)}/{MAX_PER_USER} · cancel one with /remind cancel")
        await interaction.response.send_message(embed=brand(embed, interaction.guild), ephemeral=True)

    @remind.command(name="cancel", description="Cancel one of your reminders")
    @app_commands.describe(reminder="The reminder to cancel")
    async def cancel(self, interaction: discord.Interaction, reminder: int) -> None:
        if await self.bot.reminders_db.cancel(interaction.user.id, reminder):
            await interaction.response.send_message(embed=style.ok(f"Reminder #{reminder} cancelled."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=style.warn(f"You don't have a reminder #{reminder}."),
                                                    ephemeral=True)

    @cancel.autocomplete("reminder")
    async def cancel_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
        reminders = await self.bot.reminders_db.for_user(interaction.user.id)
        matches = [r for r in reminders if current.lower() in short(r).lower()]
        return [app_commands.Choice(name=short(r)[:100], value=r.id) for r in matches[:25]]

    # --- delivery -----------------------------------------------------------------

    @tasks.loop(seconds=20)
    async def deliver(self) -> None:
        db = self.bot.reminders_db
        try:
            if not db.loaded:
                await db.refresh_next_due()
            if db.next_due is None or db.next_due > discord.utils.utcnow():
                return
            due = await db.take_due()
            await db.refresh_next_due()
        except Exception:
            log.exception("Loading due reminders failed")
            return
        for reminder in due:
            try:
                await self.send(reminder)
            except Exception:
                log.exception("Reminder %s failed", reminder.id)

    @deliver.before_loop
    async def before_deliver(self) -> None:
        await self.bot.wait_until_ready()

    async def send(self, reminder) -> bool:
        """Post in the original channel (or DMs when private or that fails). False if neither worked."""
        guild = self.bot.get_guild(reminder.guild_id) if reminder.guild_id else None
        embed = reminder_embed(reminder, guild)
        channel = guild.get_channel_or_thread(reminder.channel_id) if guild and reminder.channel_id else None
        member = guild.get_member(reminder.user_id) if guild else None
        if not reminder.private and channel is not None and member is not None:
            perms = channel.permissions_for(guild.me)
            if perms.send_messages and perms.embed_links and channel.permissions_for(member).view_channel:
                try:
                    await channel.send(f"<@{reminder.user_id}>", embed=embed,
                                       allowed_mentions=discord.AllowedMentions(users=True))
                    return True
                except discord.HTTPException as exc:
                    log.info("Reminder %s not posted in its channel (%s); trying DMs", reminder.id, exc)
        try:
            user = self.bot.get_user(reminder.user_id) or await self.bot.fetch_user(reminder.user_id)
            await user.send(embed=embed)
            return True
        except discord.HTTPException as exc:
            log.info("Reminder %s couldn't be delivered: %s", reminder.id, exc)
            return False
