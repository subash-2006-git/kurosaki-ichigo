"""Server logging: event logs in a 📂 LOGGING category, audit attribution, and 📊 SERVER STATS counters (from logging-bot).

No database: a guild has logging when its logging category exists, and channels are found by name.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from discord.ext import commands

from features.logging.services.audit_handler import AuditHandler
from features.logging.services.log_router import LogRouter

if TYPE_CHECKING:
    from core.bot import Bot

COGS = ("lifecycle", "setup", "logging_events", "stats")


async def setup(bot: Bot) -> None:
    bot.log_router = LogRouter(bot)
    bot.audit = AuditHandler(bot)
    bot.log_router.start()
    for name in COGS:
        await bot.load_extension(f"{__name__}.cogs.{name}")


async def teardown(bot: Bot) -> None:
    for name in COGS:
        with contextlib.suppress(commands.ExtensionNotLoaded):
            await bot.unload_extension(f"{__name__}.cogs.{name}")
    await bot.log_router.close()
