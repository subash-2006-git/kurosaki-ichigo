"""Voice commands: !disconnect all, !server-mute, !server-deafen (and their undo), with skips set in the panel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.backups import RestoreTarget
from features.voicecontrol import panel
from features.voicecontrol.cog import VoiceControl
from features.voicecontrol.db import VoiceControlDB

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.voicecontrol_db = VoiceControlDB(bot.db.pool)
    bot.restore_targets.append(RestoreTarget("voicecontrol.settings", "Voice command skips", reset=bot.voicecontrol_db.forget))
    bot.panel.register(panel.SECTION)
    await bot.add_cog(VoiceControl(bot))


async def teardown(bot: Bot) -> None:
    bot.panel.unregister(panel.SECTION.key)
