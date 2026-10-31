"""Quote images: turn a message into a picture of the author's avatar fading into their words."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.backups import RestoreTarget
from features.quotes import panel
from features.quotes.cog import ConversationButton, QuoteButton, Quotes, QuotesDB

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.quotes_db = QuotesDB(bot.db.pool)
    bot.restore_targets.append(RestoreTarget("quotes.settings", "Quote settings", reset=bot.quotes_db.forget))
    bot.add_dynamic_items(QuoteButton, ConversationButton)
    bot.panel.register(panel.SECTION)
    await bot.add_cog(Quotes(bot))


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(QuoteButton, ConversationButton)
    bot.panel.unregister(panel.SECTION.key)
