"""Startup notice and error reports in custom-logs (from logging-bot's bot class)."""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from features.logging.services.log_router import truncate

if TYPE_CHECKING:
    from core.bot import Bot


def traceback_tail(exc: BaseException | None, frames: int = 4) -> str:
    """The last few stack frames as "file:line in function / code" lines."""
    if exc is None:
        return "unknown"
    lines = []
    for frame in traceback.extract_tb(exc.__traceback__)[-frames:]:
        path = frame.filename.replace("\\", "/")
        short = path.split("site-packages/")[-1] if "site-packages/" in path else "/".join(path.split("/")[-2:])
        lines.append(f"{short}:{frame.lineno} in {frame.name}\n    {frame.line or ''}")
    return "\n".join(lines)


class Lifecycle(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._announced_startup = False

    async def cog_load(self) -> None:
        self.bot.error_reporters.append(self.report_error)

    async def cog_unload(self) -> None:
        if self.report_error in self.bot.error_reporters:
            self.bot.error_reporters.remove(self.report_error)

    async def report_error(self, guild: discord.Guild, title: str, error: BaseException) -> None:
        self.bot.log_router.custom(
            guild,
            f"❌ {title}",
            f"```py\n{truncate(repr(error), 1000)}\n```**Where:**\n```\n{truncate(traceback_tail(error), 1500)}\n```",
            level="error",
        )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._announced_startup:
            return  # on_ready fires again after reconnects; announce once per process.
        self._announced_startup = True
        started = discord.utils.utcnow()
        for guild in self.bot.guilds:
            self.bot.log_router.custom(
                guild, "🟢 Bot Started", f"Online since {discord.utils.format_dt(started, 'f')}.", level="success"
            )


async def setup(bot: Bot) -> None:
    await bot.add_cog(Lifecycle(bot))
