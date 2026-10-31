"""Nicknames: template-based bulk renames with /nick, auto-rename on join, and the /embed builder (from nickname-bot)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from discord.ext import commands

from features.nickname import panel
from features.nickname.db import NicknameDB
from features.nickname.services.job_manager import JobManager

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

COGS = ("nickname_commands", "member_events", "embed_builder")


async def setup(bot: Bot) -> None:
    bot.nick_db = NicknameDB(bot.db.pool)
    if interrupted := await bot.nick_db.mark_interrupted_jobs():
        log.info("Marked %d nickname job(s) interrupted by the last shutdown", interrupted)
    bot.loop.create_task(_late_sweep(bot), name="nickname-late-sweep")
    bot.nick_jobs = JobManager(bot)
    bot.status_fields.append(_status_field)
    bot.panel.register(panel.SECTION)
    for name in COGS:
        await bot.load_extension(f"{__name__}.cogs.{name}")


async def teardown(bot: Bot) -> None:
    bot.panel.unregister(panel.SECTION.key)
    for name in COGS:
        with contextlib.suppress(commands.ExtensionNotLoaded):
            await bot.unload_extension(f"{__name__}.cogs.{name}")
    if _status_field in bot.status_fields:
        bot.status_fields.remove(_status_field)
    await bot.nick_jobs.shutdown()


def _status_field(bot: Bot, guild_id: int) -> tuple[str, str] | None:
    job = bot.nick_jobs.current(guild_id)
    return ("Bulk nickname job", f"#{job.id} running: {job.processed}/{job.total}" if job else "None running")


async def _late_sweep(bot: Bot) -> None:
    """Catch jobs that were still saving progress at startup (another instance during a deploy) but then stopped."""
    await asyncio.sleep(300)
    try:
        if interrupted := await bot.nick_db.mark_interrupted_jobs():
            log.info("Marked %d silent nickname job(s) interrupted", interrupted)
    except Exception:
        log.warning("Late nickname job sweep failed", exc_info=True)
