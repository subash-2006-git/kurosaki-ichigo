"""Tickets: a button members press to open a private channel with staff, with claim, close and transcripts.

Configured entirely from the control panel (no slash commands).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from features.tickets import panel
from features.tickets.buttons import ClaimTicketButton, CloseTicketButton, OpenTicketButton
from features.tickets.db import TicketDB

if TYPE_CHECKING:
    from core.bot import Bot

BUTTONS = (OpenTicketButton, ClaimTicketButton, CloseTicketButton)


class Tickets(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        # Deleted by hand instead of with Close: free the member to open a new ticket.
        if isinstance(channel, discord.TextChannel) and channel.name.startswith("ticket-"):
            await self.bot.tickets_db.forget_channel(channel.id)


async def setup(bot: Bot) -> None:
    bot.tickets_db = TicketDB(bot.db.pool)
    bot.add_dynamic_items(*BUTTONS)
    bot.panel.register(panel.SECTION)
    await bot.add_cog(Tickets(bot))


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(*BUTTONS)
    bot.panel.unregister(panel.SECTION.key)
