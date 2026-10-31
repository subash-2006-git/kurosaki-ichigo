"""Suggestions: /suggest posts an idea with vote buttons; staff accept or deny it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from features.suggestions import panel
from features.suggestions.cog import DecideButton, Suggestions, VoteButton
from features.suggestions.db import SuggestionsDB

if TYPE_CHECKING:
    from core.bot import Bot

BUTTONS = (VoteButton, DecideButton)


async def setup(bot: Bot) -> None:
    bot.suggestions_db = SuggestionsDB(bot.db.pool)
    bot.add_dynamic_items(*BUTTONS)
    bot.panel.register(panel.SECTION)
    bot.panel.add_check(panel.check)
    await bot.add_cog(Suggestions(bot))


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(*BUTTONS)
    bot.panel.unregister(panel.SECTION.key)
    bot.panel.remove_check(panel.check)
