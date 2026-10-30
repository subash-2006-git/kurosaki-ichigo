"""Entry point: logging -> web server -> bot.

Exit codes: 0 normal stop, 1 bad configuration or login, 3 a feature asked for a restart
(start.sh / the host starts the process again).
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord

from core import config, web
from core.bot import Bot
from core.logging_setup import setup_logging

log = logging.getLogger("main")

RESTART_EXIT_CODE = 3


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
    return RESTART_EXIT_CODE if bot.restart_requested else 0


def main() -> None:
    setup_logging(config.LOG_LEVEL)
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
