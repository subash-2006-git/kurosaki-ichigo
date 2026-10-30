"""Entry point: logging -> web server -> bot.

Exit codes: 0 normal stop, 1 bad configuration or login.
"""

from __future__ import annotations

import asyncio
import logging
import random
import signal
import sys

import aiohttp
import discord

from core import config, web
from core.bot import Bot
from core.logging_setup import setup_logging

log = logging.getLogger("main")

LOGIN_CHECK_URL = "https://discord.com/api/v10/users/@me"
BACKOFF_START = 60
BACKOFF_MAX = 900


async def wait_until_discord_allows_login(token: str) -> None:
    """Block until Discord answers a login request without a rate limit.

    Shared hosting IPs (Render's free tier) get temporarily banned by Discord's Cloudflare
    (error 1015 / HTTP 429). Exiting on that makes the host restart the bot straight away,
    and every retry extends the ban, so wait it out here with backoff instead.
    Raises discord.LoginFailure for a bad token.
    """
    delay = BACKOFF_START
    async with aiohttp.ClientSession(headers={"Authorization": f"Bot {token}"}) as session:
        while True:
            try:
                async with session.get(LOGIN_CHECK_URL, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return
                    if resp.status == 401:
                        raise discord.LoginFailure("Improper token has been passed.")
                    retry_after = resp.headers.get("Retry-After")
                    reason = f"HTTP {resp.status}" + (" (Cloudflare ban on this IP)" if resp.status == 429 and not retry_after else "")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                retry_after, reason = None, f"network error {type(exc).__name__}"
            wait = max(float(retry_after), 1.0) if retry_after else delay + random.uniform(0, 15)
            log.warning("Discord login blocked: %s. Retrying in %.0fs", reason, wait)
            await asyncio.sleep(wait)
            delay = min(delay * 2, BACKOFF_MAX)


async def run() -> int:
    missing = config.missing_required()
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        return 1

    bot = Bot()
    # Bind the port first so the host's health check gets an answer while the bot logs in.
    runner = await web.start(bot, config.PORT)

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(bot.close()))
    except NotImplementedError:
        pass  # Windows: Ctrl+C still works

    try:
        await wait_until_discord_allows_login(config.DISCORD_TOKEN)
        async with bot:
            await bot.start(config.DISCORD_TOKEN)
    except discord.LoginFailure:
        log.error("Discord rejected DISCORD_TOKEN")
        return 1
    except discord.PrivilegedIntentsRequired:
        log.error("Enable the Presence, Server Members and Message Content intents in the Developer Portal")
        return 1
    finally:
        await runner.cleanup()
    return 0


def main() -> None:
    setup_logging(config.LOG_LEVEL)
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
