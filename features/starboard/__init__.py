"""Starboard: messages with enough ⭐ (or a chosen reaction) are reposted to a highlights channel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from features.starboard import panel
from features.starboard.board import Starboard
from features.starboard.db import StarboardDB

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.starboard_db = StarboardDB(bot.db.pool)
    bot.panel.register(panel.SECTION)
    await bot.add_cog(Starboard(bot))


async def teardown(bot: Bot) -> None:
    bot.panel.unregister(panel.SECTION.key)
