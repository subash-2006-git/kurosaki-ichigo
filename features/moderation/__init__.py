"""Moderation: cases, warnings, automod, anti-raid, reports, notes, roles and channel tools (from mod-bot)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from discord.ext import commands

from core.commands import hide

from features.moderation import panel
from features.moderation.case_buttons import HistoryButton, UndoButton
from features.moderation.db import ModerationDB
from features.moderation.utils.guild_cache import GuildCache

if TYPE_CHECKING:
    from core.bot import Bot

COGS = (
    "config_cog",
    "moderation",
    "warnings",
    "info",
    "notes",
    "channel",
    "roles",
    "tasks",
    "automod",
    "antiraid",
    "reports",
)


async def setup(bot: Bot) -> None:
    bot.mod_db = ModerationDB(bot.db.pool)
    bot.mod_configs = GuildCache(bot.mod_db)
    await bot.mod_db.refresh_next_scheduled()
    bot.add_dynamic_items(HistoryButton, UndoButton)
    bot.panel.register(panel.SECTION)
    bot.panel.add_check(panel.check)
    for name in COGS:
        await bot.load_extension(f"{__name__}.cogs.{name}")
    hide(bot, "config")  # the panel's Moderation section


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(HistoryButton, UndoButton)
    bot.panel.unregister(panel.SECTION.key)
    bot.panel.remove_check(panel.check)
    for name in COGS:
        with contextlib.suppress(commands.ExtensionNotLoaded):
            await bot.unload_extension(f"{__name__}.cogs.{name}")
