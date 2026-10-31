"""Reminders: /remind me pings a member about something later, in the channel or their DMs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from features.reminders.cog import Reminders
from features.reminders.db import ReminderDB

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.reminders_db = ReminderDB(bot.db.pool)
    await bot.add_cog(Reminders(bot))
