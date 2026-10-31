"""Birthdays: members save a day and month; the bot announces it and gives a role for the day."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.backups import RestoreTarget
from features.birthdays import panel
from features.birthdays.cog import Birthdays
from features.birthdays.db import BirthdayDB

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.birthdays_db = BirthdayDB(bot.db.pool)
    bot.restore_targets.append(RestoreTarget("birthdays.settings", "Birthday settings", keep=("last_run",),
                                             reset=bot.birthdays_db.forget))
    bot.panel.register(panel.SECTION)
    bot.panel.add_check(panel.check)
    await bot.add_cog(Birthdays(bot))


async def teardown(bot: Bot) -> None:
    bot.panel.unregister(panel.SECTION.key)
    bot.panel.remove_check(panel.check)
