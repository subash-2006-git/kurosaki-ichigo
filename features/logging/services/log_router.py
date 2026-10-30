"""Routes log embeds to the correct logging channel through an async delivery queue."""

from __future__ import annotations

import asyncio
import datetime
import io
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from features.logging import settings as config

if TYPE_CHECKING:
    from core.bot import Bot as LoggerBot

log = logging.getLogger(__name__)

# Embed colours per log category.
COLORS: dict[str, discord.Colour] = {
    "member": discord.Colour.blurple(),
    "join_leave": discord.Colour.green(),
    "nickname": discord.Colour.teal(),
    "profile": discord.Colour.teal(),
    "message": discord.Colour.orange(),
    "moderation": discord.Colour.red(),
    "role": discord.Colour.purple(),
    "channel": discord.Colour.blue(),
    "voice": discord.Colour.dark_teal(),
    "server": discord.Colour.gold(),
    "invite": discord.Colour.dark_green(),
    "bot": discord.Colour.dark_purple(),
    "audit": discord.Colour.light_grey(),
    "custom": discord.Colour.dark_grey(),
}

UNAVAILABLE = "*Unavailable*"


def format_duration(delta: datetime.timedelta) -> str:
    """e.g. "2h 34m 17s", "5m 3s", "42s", "1d 0h 2m 0s"."""
    seconds = max(0, int(delta.total_seconds()))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    units = [(days, "d"), (hours, "h"), (minutes, "m"), (seconds, "s")]
    while len(units) > 1 and units[0][0] == 0:
        units.pop(0)  # drop leading zero units only
    return " ".join(f"{value}{suffix}" for value, suffix in units)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def make_embed(key: str, title: str, description: str | None = None, *, colour: discord.Colour | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(title, 256),
        description=truncate(description, 4096) if description else None,
        colour=colour or COLORS.get(key, discord.Colour.default()),
        timestamp=discord.utils.utcnow(),
    )
    return embed


def add_field(embed: discord.Embed, name: str, value: object, *, inline: bool = False) -> None:
    """Add a field, substituting an explicit placeholder for empty values and respecting limits."""
    if len(embed.fields) >= 25:
        return
    text = str(value) if value not in (None, "") else UNAVAILABLE
    embed.add_field(name=truncate(name, 256), value=truncate(text, 1024), inline=inline)


@dataclass
class LogFile:
    """A file attachment stored as bytes so it can be re-sent on retry."""

    filename: str
    data: bytes

    def to_discord(self) -> discord.File:
        return discord.File(io.BytesIO(self.data), filename=self.filename)


@dataclass
class _LogItem:
    guild_id: int
    key: str
    embed: discord.Embed
    files: list[LogFile] = field(default_factory=list)


class LogRouter:
    """Resolves log channels by name (no database) and delivers embeds sequentially."""

    MAX_ATTEMPTS = 3

    def __init__(self, bot: LoggerBot) -> None:
        self.bot = bot
        self._queue: asyncio.Queue[_LogItem] = asyncio.Queue(maxsize=5000)
        self._channel_cache: dict[tuple[int, str], int] = {}
        self._warned: set[tuple[int, str]] = set()
        self._worker: asyncio.Task[None] | None = None

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="log-router")

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    # --- channel resolution ----------------------------------------------------

    @staticmethod
    def find_category(guild: discord.Guild) -> discord.CategoryChannel | None:
        return discord.utils.get(guild.categories, name=config.LOG_CATEGORY_NAME)

    def is_enabled(self, guild: discord.Guild | None) -> bool:
        """Whether logging has been set up in this guild (the logging category exists)."""
        return guild is not None and self.find_category(guild) is not None

    def is_log_channel(self, channel: object) -> bool:
        category = getattr(channel, "category", None)
        return category is not None and category.name == config.LOG_CATEGORY_NAME

    def resolve(self, guild: discord.Guild, key: str) -> discord.TextChannel | None:
        cached_id = self._channel_cache.get((guild.id, key))
        if cached_id is not None:
            channel = guild.get_channel(cached_id)
            if isinstance(channel, discord.TextChannel) and self.is_log_channel(channel):
                return channel
            self._channel_cache.pop((guild.id, key), None)

        category = self.find_category(guild)
        if category is None:
            return None
        for channel in category.text_channels:
            if config.log_channel_matches(channel.name, key):
                self._channel_cache[(guild.id, key)] = channel.id
                return channel
        return None

    def invalidate(self, guild_id: int) -> None:
        for cache_key in [k for k in self._channel_cache if k[0] == guild_id]:
            del self._channel_cache[cache_key]
        self._warned = {w for w in self._warned if w[0] != guild_id}

    # --- delivery --------------------------------------------------------------

    def send(self, guild: discord.Guild, key: str, embed: discord.Embed, files: list[LogFile] | None = None) -> None:
        """Queue a log entry. Never blocks and never raises."""
        if key not in config.LOG_CHANNELS:
            log.error("Unknown log channel key %r", key)
            return
        try:
            self._queue.put_nowait(_LogItem(guild.id, key, embed, files or []))
        except asyncio.QueueFull:
            log.warning("Log queue full; dropping %s log for guild %s", key, guild.id)

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._deliver(item)
            except Exception:
                log.exception("Unexpected error delivering %s log", item.key)
            finally:
                self._queue.task_done()

    async def _deliver(self, item: _LogItem) -> None:
        guild = self.bot.get_guild(item.guild_id)
        if guild is None:
            return

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            channel = self.resolve(guild, item.key)
            if channel is None:
                return  # Logging not set up (or this channel is missing); nothing to do.
            try:
                await channel.send(
                    embed=item.embed,
                    files=[f.to_discord() for f in item.files],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except discord.Forbidden:
                self._report_unwritable(guild, item.key, channel)
                return
            except discord.NotFound:
                # Channel was deleted between resolve and send; re-resolve on the next attempt.
                self._channel_cache.pop((guild.id, item.key), None)
            except discord.HTTPException as exc:
                if exc.status < 500:
                    log.warning("Discord rejected %s log in %s: %s", item.key, guild.id, exc)
                    return
                log.warning("Discord error %s on %s log (attempt %d)", exc.status, item.key, attempt)
            # discord.py already waits out 429 rate limits; this backoff covers 5xx errors.
            await asyncio.sleep(2**attempt)

        log.error("Giving up on %s log for guild %s after %d attempts", item.key, guild.id, self.MAX_ATTEMPTS)

    def _report_unwritable(self, guild: discord.Guild, key: str, channel: discord.TextChannel) -> None:
        warn_key = (guild.id, key)
        if warn_key in self._warned:
            return
        self._warned.add(warn_key)
        log.warning("Missing permission to send logs in #%s (%s)", channel.name, guild.name)
        if key != "custom":
            embed = make_embed("custom", "⚠️ Log Channel Not Writable", colour=discord.Colour.yellow())
            add_field(embed, "Channel", channel.mention)
            add_field(embed, "Problem", "The bot needs View Channel, Send Messages, Embed Links and Attach Files here.")
            self.send(guild, "custom", embed)

    # --- internal events -------------------------------------------------------

    def custom(self, guild: discord.Guild, title: str, description: str | None = None, *, level: str = "info") -> None:
        """Send a logger-internal event (startup, warning, error) to custom-logs."""
        colour = {
            "info": discord.Colour.dark_grey(),
            "success": discord.Colour.green(),
            "warning": discord.Colour.yellow(),
            "error": discord.Colour.red(),
        }.get(level, discord.Colour.dark_grey())
        self.send(guild, "custom", make_embed("custom", title, description, colour=colour))
