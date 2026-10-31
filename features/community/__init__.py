"""Community: welcome/goodbye messages with image cards, message + voice XP, level roles, /rank and /leaderboard (from general-bot)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import aiohttp
from discord.ext import commands

from core.commands import hide
from core.interactions import DEFER
from features.community import panel
from features.community.db import CommunityDB
from features.community.level_buttons import LeaderboardButton, RankButton
from features.community.services.guild_config import GuildConfigService
from features.community.services.leveling import LevelingService
from features.community.services.levelups import LevelUpService
from features.community.services.media import MediaStore
from features.community.services.roles import RoleService

if TYPE_CHECKING:
    from core.bot import Bot

USER_AGENT = "DiscordBot (community feature; image downloads)"

COGS = ("housekeeping", "welcome", "goodbye", "leveling", "leveling_admin", "voice_xp", "leaderboard")

# general-bot deferred every command before it ran (its commands reply through core.interactions.reply);
# core's tree only defers on request, so turn it on for this feature's top-level commands.
# Commands that open modals set {DEFER: False} on themselves, which still wins.
DEFERRED_COMMANDS = ("welcome", "goodbye", "leveling", "rank", "leaderboard")


async def setup(bot: Bot) -> None:
    bot.community_db = CommunityDB(bot.db.pool)
    bot.guild_config = GuildConfigService(bot.community_db)
    bot.media = MediaStore(bot.community_db)
    bot.leveling = LevelingService(bot.community_db)
    bot.roles = RoleService(bot.community_db, bot.guild_config)
    bot.levelups = LevelUpService(bot.guild_config, bot.roles, brand=bot.theme.brand)
    bot.add_dynamic_items(RankButton, LeaderboardButton)
    for section in panel.SECTIONS:
        bot.panel.register(section)
    bot.http_session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
    for name in COGS:
        await bot.load_extension(f"{__name__}.cogs.{name}")
    # The panel's Welcome & Goodbye and Leveling sections. Kept: /leveling xp ... and /leveling role sync.
    hide(bot, "welcome", "goodbye",
         *(f"leveling {name}" for name in ("enable", "disable", "settings", "message-xp", "message-cooldown", "voice-xp",
                                            "voice-interval", "voice-rules", "levelup", "levelup-channel", "levelup-text")),
         *(f"leveling role {name}" for name in ("add", "remove", "list", "mode")))
    for name in DEFERRED_COMMANDS:
        command = bot.tree.get_command(name)
        if command is not None:
            command.extras.setdefault(DEFER, True)


async def teardown(bot: Bot) -> None:
    for name in COGS:
        with contextlib.suppress(commands.ExtensionNotLoaded):
            await bot.unload_extension(f"{__name__}.cogs.{name}")
    bot.remove_dynamic_items(RankButton, LeaderboardButton)
    for section in panel.SECTIONS:
        bot.panel.unregister(section.key)
    if bot.http_session is not None:
        await bot.http_session.close()
