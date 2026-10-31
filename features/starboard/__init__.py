"""Starboard: messages with enough ⭐ (or a chosen reaction) are reposted to a highlights channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.backups import RestoreTarget
from features.starboard import panel
from features.starboard.board import Starboard
from features.starboard.db import StarboardDB
from features.starboard.leaderboard import StarboardTop

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.starboard_db = StarboardDB(bot.db.pool)
    bot.restore_targets.append(RestoreTarget("starboard.settings", "Starboard settings", reset=bot.starboard_db.forget))
    bot.panel.register(panel.SECTION)
    await bot.add_cog(Starboard(bot))
    await bot.add_cog(StarboardTop(bot))


async def teardown(bot: Bot) -> None:
    bot.panel.unregister(panel.SECTION.key)
