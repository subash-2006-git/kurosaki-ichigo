"""Invite tracking: which invite (and whose) each member joined with; /invites shows the counts.

Owns the invite cache; the join log asks bot.invite_tracker for the same result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from features.invites.cog import Invites
from features.invites.db import InvitesDB
from features.invites.tracker import InviteTracker

if TYPE_CHECKING:
    from core.bot import Bot


async def setup(bot: Bot) -> None:
    bot.invite_tracker = InviteTracker()
    bot.invites_db = InvitesDB(bot.db.pool)
    await bot.add_cog(Invites(bot))
