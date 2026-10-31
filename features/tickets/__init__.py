"""Tickets: a button members press to open a private channel with staff, with claim, close and transcripts.

Configured entirely from the control panel (no slash commands).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from core.ui import style
from core.ui.theme import brand
from features.tickets import panel
from features.tickets.buttons import (ClaimTicketButton, CloseTicketButton, OpenTicketButton, RateTicketButton,
                                      close_ticket, idle_step)
from features.tickets.db import TicketDB

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

BUTTONS = (OpenTicketButton, ClaimTicketButton, CloseTicketButton, RateTicketButton)


class Tickets(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.close_idle.start()

    async def cog_unload(self) -> None:
        self.close_idle.cancel()

    @tasks.loop(minutes=10)
    async def close_idle(self) -> None:
        db = self.bot.tickets_db
        try:
            if db.idle_on is None:
                db.idle_on = await db.any_idle_close()
            if not db.idle_on:
                return
            tickets = await db.idle_candidates()
        except Exception:
            log.exception("Loading idle tickets failed")
            return
        now = discord.utils.utcnow()
        for ticket in tickets:
            try:
                await self._check_idle(ticket, now)
            except Exception:
                log.exception("Auto-close check failed for ticket %s", ticket.id)

    async def _check_idle(self, ticket, now) -> None:
        channel = self.bot.get_channel(ticket.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        last_id = channel.last_message_id
        last_at = discord.utils.snowflake_time(last_id) if last_id else ticket.created_at
        step = idle_step(now, last_at, last_id, ticket.idle_warning_id, ticket.idle_close_hours, ticket.idle_warn_hours)
        if step == "warn":
            text = (f"⏰ This ticket has been quiet for a while. It will close in **{ticket.idle_warn_hours} hour(s)** "
                    "unless someone sends a message.")
            message = await channel.send(f"<@{ticket.opener_id}>", embed=brand(style.warn(text), channel.guild),
                                         allowed_mentions=discord.AllowedMentions(users=True))
            await self.bot.tickets_db.set_idle_warning(ticket.id, message.id)
        elif step == "close":
            await close_ticket(self.bot, channel, ticket, channel.guild.me,
                               why=f"Closed automatically after {ticket.idle_close_hours} hour(s) without messages.")

    @close_idle.before_loop
    async def before_close_idle(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        # Deleted by hand instead of with Close: free the member to open a new ticket.
        if isinstance(channel, discord.TextChannel) and channel.name.startswith("ticket-"):
            await self.bot.tickets_db.forget_channel(channel.id)


async def setup(bot: Bot) -> None:
    bot.tickets_db = TicketDB(bot.db.pool)
    bot.add_dynamic_items(*BUTTONS)
    bot.panel.register(panel.SECTION)
    bot.panel.add_check(panel.check)
    await bot.add_cog(Tickets(bot))


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(*BUTTONS)
    bot.panel.unregister(panel.SECTION.key)
    bot.panel.remove_check(panel.check)
