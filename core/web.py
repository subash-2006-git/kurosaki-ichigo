"""One HTTP server so the host and uptime pingers can see the bot is alive."""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)


async def start(bot: Bot, port: int) -> web.AppRunner:
    async def health(_request: web.Request) -> web.Response:
        # Gateway readiness only. The database is deliberately not queried here: pingers hit this
        # every few minutes, which would keep Neon's compute awake around the clock.
        ready = bot.is_ready() and not bot.is_closed()
        latency = bot.latency
        return web.json_response(
            {
                "status": "ok" if ready else "starting",
                "ready": ready,
                "latency_ms": None if math.isinf(latency) or math.isnan(latency) else round(latency * 1000),
                "guilds": len(bot.guilds),
                "voice_connections": len(bot.voice_clients),
                "uptime_seconds": round(time.monotonic() - bot.started_at),
            },
            status=200 if ready else 503,
        )

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text="Bot is running. See /health.")

    app = web.Application()
    # add_get also answers HEAD, which some uptime pingers use.
    app.router.add_get("/", index)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Web server listening on port %s", port)
    return runner
