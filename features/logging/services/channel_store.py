"""Which channels are the logging/stats channels, remembered by ID so renames and moves don't break them.

Every row is loaded into memory at startup, so lookups during events never touch the database.
Servers set up before IDs were stored are found by name once, then remembered by ID.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TypeVar

import asyncpg
import discord

from features.logging import settings as config

log = logging.getLogger(__name__)

LOG_CATEGORY = "category:logs"
STATS_CATEGORY = "category:stats"

ChannelT = TypeVar("ChannelT", bound=discord.abc.GuildChannel)


def log_key(key: str) -> str:
    return f"log:{key}"


def stat_key(key: str) -> str:
    return f"stat:{key}"


class ChannelStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool
        self._ids: dict[int, dict[str, int]] = defaultdict(dict)
        self._pending: set[asyncio.Task[None]] = set()

    async def load(self) -> None:
        rows = await self.pool.fetch("SELECT guild_id, key, channel_id FROM logging.channels")
        self._ids.clear()
        for row in rows:
            self._ids[row["guild_id"]][row["key"]] = row["channel_id"]
        log.info("Loaded %d logging channel(s) for %d guild(s)", len(rows), len(self._ids))

    # --- writes ----------------------------------------------------------------

    async def save(self, guild_id: int, mapping: dict[str, int]) -> None:
        """Remember several channels now (used by /setup, which reports failures)."""
        if not mapping:
            return
        self._ids[guild_id].update(mapping)
        await self.pool.executemany(
            "INSERT INTO logging.channels (guild_id, key, channel_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, key) DO UPDATE SET channel_id = EXCLUDED.channel_id, updated_at = NOW()",
            [(guild_id, key, channel_id) for key, channel_id in mapping.items()],
        )

    def remember(self, guild_id: int, key: str, channel_id: int) -> None:
        """Remember one channel found by name. Memory updates now; the database write runs in the background."""
        if self._ids[guild_id].get(key) == channel_id:
            return
        self._background(self.save(guild_id, {key: channel_id}), f"remember {key}")

    def forget_channel(self, guild_id: int, channel_id: int) -> None:
        keys = [k for k, cid in self._ids.get(guild_id, {}).items() if cid == channel_id]
        if not keys:
            return
        for key in keys:
            del self._ids[guild_id][key]
        self._background(
            self.pool.execute("DELETE FROM logging.channels WHERE guild_id = $1 AND channel_id = $2", guild_id, channel_id),
            f"forget {channel_id}",
        )

    async def flush(self) -> None:
        """Wait for background writes (call before the pool closes)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def _background(self, coro, what: str) -> None:
        async def run() -> None:
            try:
                await coro
            except Exception:
                log.exception("Logging channel store: %s failed", what)

        task = asyncio.create_task(run())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    # --- lookups (memory only, plus a one-time name fallback) ---------------------

    def _stored(self, guild: discord.Guild, key: str, kind: type[ChannelT]) -> ChannelT | None:
        channel_id = self._ids.get(guild.id, {}).get(key)
        channel = guild.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, kind) else None

    def log_category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        category = self._stored(guild, LOG_CATEGORY, discord.CategoryChannel)
        if category is None and (category := discord.utils.get(guild.categories, name=config.LOG_CATEGORY_NAME)):
            self.remember(guild.id, LOG_CATEGORY, category.id)
        return category

    def stats_category(self, guild: discord.Guild) -> discord.CategoryChannel | None:
        category = self._stored(guild, STATS_CATEGORY, discord.CategoryChannel)
        if category is None and (category := discord.utils.get(guild.categories, name=config.STATS_CATEGORY_NAME)):
            self.remember(guild.id, STATS_CATEGORY, category.id)
        return category

    def log_channel(self, guild: discord.Guild, key: str) -> discord.TextChannel | None:
        channel = self._stored(guild, log_key(key), discord.TextChannel)
        if channel is None and (category := self.log_category(guild)):
            channel = next((c for c in category.text_channels if config.log_channel_matches(c.name, key)), None)
            if channel is not None:
                self.remember(guild.id, log_key(key), channel.id)
        return channel

    def counters(self, guild: discord.Guild) -> dict[str, discord.VoiceChannel]:
        found: dict[str, discord.VoiceChannel] = {}
        category: discord.CategoryChannel | None = None
        for key in config.STATS_COUNTERS:
            channel = self._stored(guild, stat_key(key), discord.VoiceChannel)
            if channel is None:
                category = category or self.stats_category(guild)
                if category is None:
                    continue
                channel = next((c for c in category.voice_channels if config.stats_channel_matches(c.name, key)), None)
                if channel is not None:
                    self.remember(guild.id, stat_key(key), channel.id)
            if channel is not None:
                found[key] = channel
        return found

    def is_log_channel(self, channel: object) -> bool:
        guild = getattr(channel, "guild", None)
        channel_id = getattr(channel, "id", None)
        if guild is None or channel_id is None:
            return False
        stored = self._ids.get(guild.id, {})
        if any(cid == channel_id for key, cid in stored.items() if key.startswith("log:")):
            return True
        category = self.log_category(guild)
        return category is not None and getattr(channel, "category_id", None) == category.id

    def is_counter(self, channel: object) -> bool:
        guild = getattr(channel, "guild", None)
        channel_id = getattr(channel, "id", None)
        if guild is None or channel_id is None:
            return False
        return any(cid == channel_id for key, cid in self._ids.get(guild.id, {}).items() if key.startswith("stat:"))
