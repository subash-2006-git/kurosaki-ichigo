"""The single Bot subclass every feature runs inside."""

from __future__ import annotations

import logging
import sys
import time
import traceback
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands
from discord.ext import commands

from core import config, errors, migrate
from core.db import Database
from core.interactions import CoreTree
from core.ui import theme
from core.ui.theme import ThemeStore

log = logging.getLogger(__name__)

# Loaded in order. Features are appended here as they are merged in.
EXTENSIONS: tuple[str, ...] = (
    "core.cogs.system",
    "core.panel",
    "features.moderation",
    "features.logging",
    "features.nickname",
    "features.community",
    "features.tickets",
    "features.antinuke",
    "features.selfroles",
    "features.starboard",
    "features.quotes",
    "features.privacy",
)

# A feature that can surface errors in a guild (the logging feature's custom-logs channel)
# registers one of these; core calls them for unexpected command and event errors.
ErrorReporter = Callable[[discord.Guild, str, BaseException], Awaitable[None]]
# A feature can add one line to /status: (bot, guild_id) -> (field name, value) or None.
StatusField = Callable[["Bot", int], "tuple[str, str] | None"]
# A feature that removes members as they join (anti-raid) registers one of these, so greetings
# and auto-renames skip them. Called as check(member, leaving=False) on join ("are you about to
# remove them?") and check(member, leaving=True) on leave ("did you just remove them?").
JoinFilter = Callable[..., Awaitable[bool]]
# Told about actions people take *through* the bot (e.g. /ban), which the audit log credits to the bot:
# (guild, actor_id, action, target_id). Anti-nuke counts them against the real actor.
ActionWatcher = Callable[[discord.Guild, int, str, "int | None"], Awaitable[None]]


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True          # joins/leaves, role sync, anti-raid, nickname jobs
    intents.presences = True        # online counter in server stats
    intents.message_content = True  # automod, message edit/delete logs
    return intents


class Bot(commands.Bot):
    def __init__(self) -> None:
        # The bot never joins voice (voice XP and voice logs only read voice states), so skip the audio-library warnings.
        discord.VoiceClient.warn_nacl = False
        discord.VoiceClient.warn_dave = False
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(),
            help_command=None,
            tree_cls=CoreTree,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
            # Recent messages kept in memory (across all servers) so edit/delete logs can show the old text.
            max_messages=5000,
            **({"owner_ids": set(config.OWNER_IDS)} if config.OWNER_IDS else {}),
        )
        self.started_at = time.monotonic()
        self.db = Database(config.DATABASE_URL, ssl=config.DATABASE_SSL)
        self.error_reporters: list[ErrorReporter] = []
        self.status_fields: list[StatusField] = []
        self.join_filters: list[JoinFilter] = []
        self.action_watchers: list[ActionWatcher] = []

    async def setup_hook(self) -> None:
        await self.db.connect()
        await migrate.run(self.db)
        self.theme = ThemeStore(self.db.pool)
        await self.theme.load()
        theme.activate(self.theme)
        self.tree.on_error = self.on_app_command_error
        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.info("Loaded %s", extension)
        await self.sync_commands()

    async def sync_commands(self) -> None:
        """Sync slash commands. A failed sync is logged, never fatal: the bot still starts with
        whatever commands Discord already has (e.g. after it was removed from DEV_GUILD_ID)."""
        try:
            if config.DEV_GUILD_ID:
                guild = discord.Object(id=config.DEV_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d commands to dev guild %s", len(synced), config.DEV_GUILD_ID)
            elif config.SYNC_COMMANDS:
                synced = await self.tree.sync()
                log.info("Synced %d global commands", len(synced))
            else:
                log.info("Command sync skipped (set DEV_GUILD_ID or SYNC_COMMANDS=true)")
        except discord.Forbidden:
            log.error(
                "Command sync failed: no access to DEV_GUILD_ID %s. Is the bot still in that server? "
                "Fix DEV_GUILD_ID, or clear it and set SYNC_COMMANDS=true for global commands.",
                config.DEV_GUILD_ID,
            )
        except discord.HTTPException as exc:
            log.error("Command sync failed (%s); continuing with the commands Discord already has", exc)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d guilds", self.user, self.user.id, len(self.guilds))

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await errors.on_app_command_error(self, interaction, error)

    async def on_error(self, event: str, *args, **kwargs) -> None:
        if self.is_closed():
            # Work cut off by shutdown (e.g. the database pool closing under it) is expected.
            log.info("Event %s stopped by shutdown: %r", event, sys.exc_info()[1])
            return
        log.exception("Unhandled error in event %s", event)
        guild = next((g for g in (_guild_of(a) for a in args) if g is not None), None)
        exc = sys.exc_info()[1]
        if guild is not None and exc is not None:
            await self.report_error(guild, f"Event error in {event}", exc)

    async def report_error(self, guild: discord.Guild | None, title: str, error: BaseException) -> None:
        if guild is None:
            return
        for reporter in self.error_reporters:
            try:
                await reporter(guild, title, error)
            except Exception:
                log.warning("Error reporter failed:\n%s", traceback.format_exc())

    async def member_turned_away(self, member: discord.Member, *, leaving: bool = False) -> bool:
        """True if a feature is removing (or, with leaving=True, just removed) this member, e.g. anti-raid,
        so others shouldn't greet, rename or say goodbye to them."""
        for check in self.join_filters:
            try:
                if await check(member, leaving=leaving):
                    return True
            except Exception:
                log.exception("Join filter %r failed", check)
        return False

    async def notify_action(self, guild: discord.Guild, actor_id: int, action: str, target_id: int | None) -> None:
        """Tell watchers (anti-nuke) that `actor_id` did `action` through one of the bot's commands. Never raises."""
        for watcher in self.action_watchers:
            try:
                await watcher(guild, actor_id, action, target_id)
            except Exception:
                log.exception("Action watcher %r failed", watcher)

    async def close(self) -> None:
        await super().close()
        await self.db.close()


def _guild_of(obj: object) -> discord.Guild | None:
    if isinstance(obj, discord.Guild):
        return obj
    guild = getattr(obj, "guild", None)
    return guild if isinstance(guild, discord.Guild) else None
