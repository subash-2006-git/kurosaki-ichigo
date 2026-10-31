"""Self-roles: pronoun and DM-status pickers that members use to give themselves roles. Set up from the panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.backups import RestoreTarget
from features.selfroles import panel
from features.selfroles.buttons import SelfRoleButton
from features.selfroles.db import SelfRolesDB

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.selfroles_db = SelfRolesDB(bot.db.pool)
    bot.restore_targets += [RestoreTarget("selfroles.roles", "Self-role options"),
                            RestoreTarget("selfroles.menus", "Posted self-role pickers")]
    bot.add_dynamic_items(SelfRoleButton)
    bot.panel.register(panel.SECTION)


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(SelfRoleButton)
    bot.panel.unregister(panel.SECTION.key)
