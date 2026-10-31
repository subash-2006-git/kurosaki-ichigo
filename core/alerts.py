"""Error alerts for the bot's operators: anything logged at ERROR is sent to them on Discord.

A logging handler catches every ERROR record (failed commands, event errors, background loops,
discord.py itself) and queues it; a task sends it to ALERT_CHANNEL_ID if set, otherwise as a DM to
each operator (OWNER_IDS, or the application's owner/team). The same error is sent at most once an
hour (the next alert says how many repeats were held back), and at most MAX_PER_HOUR alerts in all.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections import Counter, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from core import config

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

WINDOW = 3600         # seconds: the same error is sent once per window
MAX_PER_HOUR = 10     # alerts of any kind
TRACE_CHARS = 1500


@dataclass(frozen=True)
class Alert:
    key: tuple[str, str, str]
    source: str
    message: str
    trace: str | None
    repeats: int  # held back since the last alert for this key


def alert_key(record: logging.LogRecord) -> tuple[str, str, str]:
    """Errors are "the same" when they come from the same logger, message template and exception type."""
    exc_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ""
    return record.name, str(record.msg), exc_type


class Throttle:
    def __init__(self, window: float = WINDOW, max_per_window: int = MAX_PER_HOUR) -> None:
        self.window, self.max = window, max_per_window
        self._last: dict[tuple, float] = {}
        self._held: Counter[tuple] = Counter()
        self._sent: deque[float] = deque()

    def check(self, key: tuple, now: float) -> int | None:
        """None = hold this one back; otherwise send, with how many repeats were held back before it."""
        while self._sent and now - self._sent[0] >= self.window:
            self._sent.popleft()
        last = self._last.get(key)
        if (last is not None and now - last < self.window) or len(self._sent) >= self.max:
            self._held[key] += 1
            return None
        self._last[key] = now
        self._sent.append(now)
        return self._held.pop(key, 0)


class AlertHandler(logging.Handler):
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue[Alert], throttle: Throttle) -> None:
        super().__init__(logging.ERROR)
        self.loop, self.queue, self.throttle = loop, queue, throttle

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:
            return  # never alert about failing to alert
        try:
            key = alert_key(record)
            repeats = self.throttle.check(key, time.monotonic())
            if repeats is None:
                return
            trace = "".join(traceback.format_exception(*record.exc_info)) if record.exc_info else None
            alert = Alert(key, record.name, record.getMessage(), trace, repeats)
            # emit() can run on other threads (executors); hand over to the bot's loop.
            self.loop.call_soon_threadsafe(self.queue.put_nowait, alert)
        except Exception:
            self.handleError(record)


def alert_embed(alert: Alert) -> discord.Embed:
    embed = discord.Embed(title="⚠️ Bot error", description=alert.message[:1000], colour=discord.Colour.red(),
                          timestamp=discord.utils.utcnow())
    embed.add_field(name="Where", value=f"`{alert.source}`", inline=True)
    if alert.trace:
        embed.add_field(name="Traceback (end)", value=f"```py\n{alert.trace[-TRACE_CHARS:]}\n```"[:1024], inline=False)
    footer = "The same error is sent at most once an hour."
    if alert.repeats:
        footer = f"Happened {alert.repeats} more time(s) since the last alert. " + footer
    embed.set_footer(text=footer)
    return embed


class ErrorAlerts:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.queue: asyncio.Queue[Alert] = asyncio.Queue()
        self.handler: AlertHandler | None = None
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.handler = AlertHandler(asyncio.get_running_loop(), self.queue, Throttle())
        logging.getLogger().addHandler(self.handler)
        self.task = asyncio.create_task(self._run(), name="error-alerts")

    def stop(self) -> None:
        if self.handler is not None:
            logging.getLogger().removeHandler(self.handler)
            self.handler = None
        if self.task is not None:
            self.task.cancel()

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            alert = await self.queue.get()
            try:
                await self._send(alert_embed(alert))
            except Exception as exc:  # logged below ERROR on purpose: see AlertHandler.emit
                log.warning("Couldn't send an error alert: %r", exc)

    async def _send(self, embed: discord.Embed) -> None:
        if config.ALERT_CHANNEL_ID:
            channel = self.bot.get_channel(config.ALERT_CHANNEL_ID)
            if isinstance(channel, discord.abc.Messageable):
                await channel.send(embed=embed)
                return
        for user_id in await self._operators():
            try:
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                await user.send(embed=embed)
            except discord.HTTPException as exc:
                log.warning("Couldn't DM error alert to %s: %s", user_id, exc)

    async def _operators(self) -> set[int]:
        if config.OWNER_IDS:
            return set(config.OWNER_IDS)
        app = await self.bot.application_info()
        if app.team:
            return {m.id for m in app.team.members}
        return {app.owner.id}
