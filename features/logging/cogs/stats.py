"""Server stats counters: five voice channels whose names show live values."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from features.logging import settings as config

if TYPE_CHECKING:
    from core.bot import Bot as LoggerBot

log = logging.getLogger(__name__)

# Discord allows roughly two channel renames per channel every ten minutes.
RENAME_LIMIT = 2
RENAME_WINDOW = 600.0


def compute_stats(guild: discord.Guild) -> dict[str, int]:
    members = guild.members
    bots = sum(1 for m in members if m.bot)
    total = max(guild.member_count or 0, len(members))
    online = sum(1 for m in members if m.status is not discord.Status.offline)
    return {
        "total": total,
        "humans": total - bots,
        "bots": bots,
        "online": online,
        "boosts": guild.premium_subscription_count or 0,
    }


class Stats(commands.Cog):
    def __init__(self, bot: LoggerBot) -> None:
        self.bot = bot
        self._dirty: set[int] = set()
        self._renames: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=RENAME_LIMIT))
        self._warned: set[int] = set()
        self._inflight: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self.updater.change_interval(seconds=config.STATS_UPDATE_INTERVAL)
        self.reconciler.change_interval(seconds=config.STATS_RECONCILE_INTERVAL)

    async def cog_load(self) -> None:
        self.updater.start()
        self.reconciler.start()

    async def cog_unload(self) -> None:
        self.updater.cancel()
        self.reconciler.cancel()
        for task in self._tasks:
            task.cancel()

    def mark_dirty(self, guild: discord.Guild | None) -> None:
        if guild is not None:
            self._dirty.add(guild.id)

    # --- background loops ------------------------------------------------------

    @tasks.loop(seconds=30)
    async def updater(self) -> None:
        for guild_id in list(self._dirty):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                self._dirty.discard(guild_id)
                continue
            try:
                if await self.refresh(guild):
                    self._dirty.discard(guild_id)
            except Exception:
                log.exception("Stats refresh failed for guild %s", guild_id)

    @tasks.loop(seconds=600)
    async def reconciler(self) -> None:
        for guild in self.bot.guilds:
            self.mark_dirty(guild)

    @updater.before_loop
    @reconciler.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    # --- refresh ---------------------------------------------------------------

    def _can_rename(self, channel_id: int) -> bool:
        history = self._renames[channel_id]
        return len(history) < RENAME_LIMIT or time.monotonic() - history[0] >= RENAME_WINDOW

    async def refresh(self, guild: discord.Guild) -> bool:
        """Start renames for counters whose value changed. Returns False if some must wait."""
        counters = self.bot.log_channels.counters(guild)
        if not counters:
            return True
        values = compute_stats(guild)
        complete = True
        for key, channel in counters.items():
            desired = config.stats_channel_name(key, values[key])
            if channel.name == desired:
                continue
            complete = False  # Re-check next tick until the name is confirmed.
            if channel.id in self._inflight or not self._can_rename(channel.id):
                continue  # Already renaming, or the rate-limit budget is used.
            # Each rename runs on its own: if Discord rate-limits one counter (discord.py then
            # waits up to ~10 minutes), the other counters keep updating.
            self._inflight.add(channel.id)
            task = asyncio.create_task(self._rename(guild, channel, desired), name=f"stats-rename-{channel.id}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return complete

    async def _rename(self, guild: discord.Guild, channel: discord.VoiceChannel, desired: str) -> None:
        started = time.monotonic()
        try:
            await channel.edit(name=desired, reason="Server stats update")
            waited = time.monotonic() - started
            if waited > 5:
                log.info("Renamed %r after waiting %.0fs for Discord's rate limit", desired, waited)
        except discord.Forbidden:
            if guild.id not in self._warned:
                self._warned.add(guild.id)
                self.bot.log_router.custom(
                    guild,
                    "⚠️ Stats Update Failed",
                    f"Missing permission to rename {channel.mention}. The bot needs Manage Channels "
                    "on the stats category.",
                    level="warning",
                )
        except discord.HTTPException as exc:
            log.warning("Could not rename stats channel %s: %s", channel.id, exc)
        except Exception:
            log.exception("Unexpected error renaming stats channel %s", channel.id)
        finally:
            # Failed attempts count too, so a persistent error can't retry every tick.
            self._renames[channel.id].append(time.monotonic())
            self._inflight.discard(channel.id)
            # The value may have changed again while we waited; check on the next tick.
            self.mark_dirty(guild)

    # --- event triggers --------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in self.bot.guilds:
            self.mark_dirty(guild)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        self.mark_dirty(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        self.mark_dirty(member.guild)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member) -> None:
        if (before.status is discord.Status.offline) != (after.status is discord.Status.offline):
            self.mark_dirty(after.guild)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if before.premium_subscription_count != after.premium_subscription_count:
            self.mark_dirty(after)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        self._renames.pop(channel.id, None)


async def setup(bot: LoggerBot) -> None:
    await bot.add_cog(Stats(bot))
