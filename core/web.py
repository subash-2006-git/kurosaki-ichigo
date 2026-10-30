"""One HTTP server: health for Render/uptime pingers, plus the legal pages the Developer Portal links to."""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

from aiohttp import web

from core import config, legal

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

    privacy_html = legal.privacy_page()
    terms_html = legal.terms_page()

    async def privacy(_request: web.Request) -> web.Response:
        return web.Response(text=privacy_html, content_type="text/html")

    async def terms(_request: web.Request) -> web.Response:
        return web.Response(text=terms_html, content_type="text/html")

    async def index(_request: web.Request) -> web.Response:
        return web.Response(text=f"{config.BOT_NAME or 'Bot'} is running. See /health, /privacy, /terms.")

    app = web.Application()
    # add_get also answers HEAD, which some uptime pingers use.
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/privacy", privacy)
    app.router.add_get("/terms", terms)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Web server listening on port %s", port)
    return runner
