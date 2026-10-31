"""The single slash-command error handler (merged from every source bot's own handler)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)


def format_permissions(names: list[str]) -> str:
    return ", ".join(f"**{n.replace('_', ' ').replace('guild', 'server').title()}**" for n in names)


def user_message(error: app_commands.AppCommandError) -> str | None:
    """Friendly text for expected errors; None means unexpected (log it and report it)."""
    if isinstance(error, app_commands.MissingPermissions):
        return f"You need the {format_permissions(error.missing_permissions)} permission(s) to use this."
    if isinstance(error, app_commands.BotMissingPermissions):
        return f"I need the {format_permissions(error.missing_permissions)} permission(s) to do that."
    if isinstance(error, app_commands.CommandOnCooldown):
        return f"Slow down! Try again in {error.retry_after:.0f}s."
    if isinstance(error, app_commands.NoPrivateMessage):
        return "This command only works in a server."
    if isinstance(error, app_commands.CheckFailure):
        return "You can't use this command here."
    original = getattr(error, "original", error)
    if isinstance(original, discord.Forbidden):
        return "I don't have permission to do that. Check my role position and channel permissions."
    return None


async def on_app_command_error(bot: Bot, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    message = user_message(error)
    if message is None:
        original = getattr(error, "original", error)
        command = interaction.command.qualified_name if interaction.command else "unknown"
        log.error("Error in /%s (guild %s, user %s)", command, interaction.guild_id, interaction.user.id, exc_info=original)
        await bot.report_error(interaction.guild, f"Command error in /{command}", original)
        message = "Something went wrong while running that command. It has been logged."

    embed = discord.Embed(description=f"❌ {message}", colour=discord.Colour.red())
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not send error reply for interaction %s", interaction.id)


def text_command_message(error: commands.CommandError) -> str | None:
    """Friendly text for expected errors in text (prefix) commands; None means unexpected."""
    if isinstance(error, commands.MissingPermissions):
        return f"You need the {format_permissions(error.missing_permissions)} permission(s) to use this."
    if isinstance(error, commands.BotMissingPermissions):
        return f"I need the {format_permissions(error.missing_permissions)} permission(s) to do that."
    if isinstance(error, commands.NoPrivateMessage):
        return "This command only works in a server."
    if isinstance(error, commands.CommandOnCooldown):
        return f"Slow down! Try again in {error.retry_after:.0f}s."
    if isinstance(error, (commands.UserInputError, commands.CheckFailure)):
        return str(error) or "You can't use this command here."
    original = getattr(error, "original", error)
    if isinstance(original, discord.Forbidden):
        return "I don't have permission to do that. Check my role position and channel permissions."
    return None


async def on_command_error(bot: Bot, ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return  # "!something" meant for another bot
    message = text_command_message(error)
    if message is None:
        original = getattr(error, "original", error)
        command = ctx.command.qualified_name if ctx.command else "unknown"
        log.error("Error in %s%s (guild %s, user %s)", ctx.prefix, command, ctx.guild and ctx.guild.id, ctx.author.id,
                  exc_info=original)
        await bot.report_error(ctx.guild, f"Command error in {ctx.prefix}{command}", original)
        message = "Something went wrong while running that command. It has been logged."
    try:
        await ctx.reply(embed=discord.Embed(description=f"❌ {message}", colour=discord.Colour.red()), mention_author=False)
    except discord.HTTPException:
        log.warning("Could not send error reply for message %s", ctx.message.id)
