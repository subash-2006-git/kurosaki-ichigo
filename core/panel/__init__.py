"""Control panel: /panel posts a permanent message whose buttons open each feature's settings editor."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.panel import access, appearance
from core.panel.service import WHO_CAN_USE, PanelEntry, PanelSection, PanelService
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot


class Panel(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @app_commands.command(name="panel", description="Post the control panel: buttons to set up every feature.")
    @app_commands.describe(channel="Where to post it (default: here). Use a staff-only channel.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=style.fail("Pick a text channel for the panel."), ephemeral=True)
            return
        perms = target.permissions_for(target.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                embed=style.fail(f"I need **View Channel** and **Send Messages** in {target.mention}."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        message = await self.bot.panel.post(target)
        await interaction.followup.send(
            embed=style.ok(
                f"Control panel posted in {target.mention}: {message.jump_url}\n"
                f"{WHO_CAN_USE()} Any older panel was removed."
            ),
            ephemeral=True,
        )


async def bot_role_on_top(bot: Bot, guild: discord.Guild) -> tuple[bool, str]:
    top = max((r for r in guild.roles if not r.is_default()), key=lambda r: r.position, default=None)
    return (top is None or guild.me.top_role == top,
            "My role is at the top of **Server Settings → Roles**, so moderation and anti-nuke can act on everyone")


async def setup(bot: Bot) -> None:
    bot.panel = PanelService(bot)
    bot.panel.add_check(bot_role_on_top)
    bot.add_dynamic_items(PanelEntry)
    bot.panel.register(PanelSection(
        key="access", label="Access", emoji="🔐", order=1, status=access.status, open=access.AccessEditor.open,
    ))
    bot.panel.register(PanelSection(
        key="appearance", label="Appearance", emoji=style.PALETTE, order=90,
        status=appearance.status, open=appearance.AppearanceEditor.open,
    ))
    await bot.add_cog(Panel(bot))


async def teardown(bot: Bot) -> None:
    bot.remove_dynamic_items(PanelEntry)
