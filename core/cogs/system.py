"""/ping, /status and /sync — the commands every feature shares."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from core import config
from core.bot import Bot
from core.interactions import reply

log = logging.getLogger(__name__)

DB_TIMEOUT = 15.0  # a suspended Neon compute needs a few seconds to wake
SLOW_GATEWAY_MS = 500
SLOW_DATABASE_MS = 250


def format_duration(seconds: float) -> str:
    """e.g. "2d 3h", "14m", or "45s" when under a minute."""
    seconds = int(seconds)
    days, rest = divmod(seconds, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{value}{unit}" for value, unit in ((days, "d"), (hours, "h"), (minutes, "m")) if value]
    return " ".join(parts[:2]) if parts else f"{secs}s"


def latency_ms(seconds: float | None) -> int | None:
    if seconds is None or math.isinf(seconds) or math.isnan(seconds):
        return None
    return round(seconds * 1000)


def status_embed(
    *,
    gateway_ms: int | None,
    database_ms: int | None,
    database_error: str | None,
    uptime_seconds: float,
    guilds: int,
    voice_connections: int,
    version: str | None,
) -> discord.Embed:
    """Green when healthy, orange when slow, red when the database is down."""
    if database_error is not None:
        colour, summary = discord.Colour.red(), "🔴 Database is not responding"
    elif gateway_ms is None or gateway_ms > SLOW_GATEWAY_MS or (database_ms or 0) > SLOW_DATABASE_MS:
        colour, summary = discord.Colour.orange(), "🟠 Up, but slow"
    else:
        colour, summary = discord.Colour.green(), "🟢 All systems normal"

    embed = discord.Embed(title="Bot status", description=summary, colour=colour)
    embed.add_field(name="Discord", value=f"✅ Connected\n{gateway_ms} ms" if gateway_ms is not None else "⚠️ Reconnecting")
    embed.add_field(
        name="Database",
        value=f"❌ Down\n{database_error}" if database_error is not None else f"✅ Up\n{database_ms} ms",
    )
    embed.add_field(name="Uptime", value=format_duration(uptime_seconds))
    embed.add_field(name="Servers", value=f"{guilds:,}")
    embed.add_field(name="Voice connections", value=str(voice_connections))
    if version:
        embed.add_field(name="Version", value=f"`{version}`")
    embed.set_footer(text="Discord = gateway heartbeat · Database = one SELECT 1 round trip")
    return embed


class System(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="Check that the bot is online.")
    async def ping(self, interaction: discord.Interaction) -> None:
        start = time.perf_counter()
        await interaction.response.send_message("Pinging…", ephemeral=True)
        round_trip = round((time.perf_counter() - start) * 1000)
        gateway = latency_ms(self.bot.latency)
        await interaction.edit_original_response(
            content=f"🏓 Pong! Gateway {gateway if gateway is not None else '?'} ms · response {round_trip} ms "
            f"· up {format_duration(time.monotonic() - self.bot.started_at)}"
        )

    @app_commands.command(name="status", description="Show whether the bot and database are up, and their latency.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        database_ms: int | None = None
        database_error: str | None = None
        try:
            database_ms = latency_ms(await self.bot.db.ping(DB_TIMEOUT))
        except asyncio.TimeoutError:
            database_error = f"No answer within {DB_TIMEOUT:.0f}s"
        except Exception as exc:
            # The exception type is enough to diagnose without leaking connection details.
            database_error = type(exc).__name__
            log.warning("Status check: database ping failed: %r", exc)

        commit = os.getenv("RENDER_GIT_COMMIT")  # set by Render for each deploy
        embed = status_embed(
            gateway_ms=latency_ms(self.bot.latency),
            database_ms=database_ms,
            database_error=database_error,
            uptime_seconds=time.monotonic() - self.bot.started_at,
            guilds=len(self.bot.guilds),
            voice_connections=len(self.bot.voice_clients),
            version=commit[:7] if commit else None,
        )
        await reply(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="sync", description="Bot owner only: re-sync slash commands with Discord.")
    @app_commands.describe(scope="guild: this server only (instant) · global: everywhere · clear-guild: remove this server's copies")
    @app_commands.default_permissions(administrator=True)
    async def sync(
        self, interaction: discord.Interaction, scope: Literal["guild", "global", "clear-guild"] = "guild"
    ) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("Only the bot owner can use this.", ephemeral=True)
            return
        if scope != "global" and interaction.guild is None:
            await interaction.response.send_message("Run this in a server, or use scope: global.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        if scope == "global":
            synced = await self.bot.tree.sync()
            text = f"Synced {len(synced)} global commands. They can take up to an hour to show everywhere."
        elif scope == "guild":
            self.bot.tree.copy_global_to(guild=interaction.guild)
            synced = await self.bot.tree.sync(guild=interaction.guild)
            text = f"Synced {len(synced)} commands to this server."
        else:
            self.bot.tree.clear_commands(guild=interaction.guild)
            await self.bot.tree.sync(guild=interaction.guild)
            text = "Removed this server's copies. Global commands still apply."
        log.info("/sync %s by %s: %s", scope, interaction.user.id, text)
        await reply(interaction, text, ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild %s (%s, %s members)", guild.name, guild.id, guild.member_count)
        if config.DEV_GUILD_ID == guild.id:
            await self.bot.sync_commands()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        log.info("Removed from guild %s (%s)", guild.name, guild.id)


async def setup(bot: Bot) -> None:
    await bot.add_cog(System(bot))
