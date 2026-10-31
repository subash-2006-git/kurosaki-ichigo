"""Anti-nuke: stop accounts that mass-delete channels/roles, mass-ban/kick, spam webhooks, add bots or hand out
dangerous permissions; undo the damage and alert staff. Off until a server turns it on in the panel."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from discord.ext import commands

from core.backups import RestoreTarget
from features.antinuke import panel
from features.antinuke.db import AntinukeDB

if TYPE_CHECKING:
    from core.bot import Bot

COGS = ("antinuke",)


async def setup(bot: Bot) -> None:
    bot.antinuke_db = AntinukeDB(bot.db.pool)
    bot.restore_targets.append(RestoreTarget("antinuke.settings", "Anti-nuke settings", reset=bot.antinuke_db.forget))
    bot.panel.register(panel.SECTION)
    bot.panel.add_check(panel.check)
    for name in COGS:
        await bot.load_extension(f"{__name__}.cogs.{name}")


async def teardown(bot: Bot) -> None:
    bot.panel.unregister(panel.SECTION.key)
    bot.panel.remove_check(panel.check)
    for name in COGS:
        with contextlib.suppress(commands.ExtensionNotLoaded):
            await bot.unload_extension(f"{__name__}.cogs.{name}")
