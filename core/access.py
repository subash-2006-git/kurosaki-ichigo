"""Who may use the bot's commands and the control panel.

With OWNER_ONLY on (the default), only the bot's owners can: the user IDs in OWNER_IDS, or the
Discord application's owner/team when OWNER_IDS is empty. Buttons members use (opening tickets,
self-role pickers, votes, ratings…) aren't commands and keep working for everyone.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from core import config

NOT_ALLOWED = "Only the bot's owners can use this bot's commands."


async def is_allowed(bot: commands.Bot, user: discord.abc.User) -> bool:
    return not config.OWNER_ONLY or await bot.is_owner(user)
