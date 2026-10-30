import re

import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import embeds
from features.moderation.utils.actions import audit_reason
from features.moderation.utils.lockdown import lock_channel, lock_server, unlock_channel, unlock_server
from features.moderation.utils.modlog import send_log

LINK_RE = re.compile(r"https?://", re.IGNORECASE)


class Channel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="purge", description="Bulk-delete recent messages")
    @app_commands.describe(amount="How many matching messages to delete",
                           user="Only messages from this user", contains="Only messages containing this text",
                           bots="Only messages from bots", links="Only messages containing links")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 500],
                    user: discord.User | None = None, contains: str | None = None,
                    bots: bool = False, links: bool = False):
        await interaction.response.defer(ephemeral=True)
        filtered = bool(user or contains or bots or links)
        deleted_count = 0

        def check(message: discord.Message) -> bool:
            nonlocal deleted_count
            if message.pinned or deleted_count >= amount:
                return False
            if user and message.author.id != user.id:
                return False
            if contains and contains.lower() not in message.content.lower():
                return False
            if bots and not message.author.bot:
                return False
            if links and not LINK_RE.search(message.content):
                return False
            deleted_count += 1
            return True

        # With filters, scan further back so we can still find `amount` matches.
        limit = min(amount * 5, 1000) if filtered else amount
        deleted = await interaction.channel.purge(limit=limit, check=check, before=interaction.created_at,
                                                  reason=audit_reason(interaction.user, "Purge"))
        await interaction.followup.send(embed=embeds.success(
            f"Deleted {len(deleted)} message(s). Messages older than 14 days are deleted slowly or skipped."))
        await send_log(self.bot, interaction.guild, embeds.info(
            "Messages purged",
            f"{interaction.user.mention} deleted {len(deleted)} message(s) in {interaction.channel.mention}"))

    @app_commands.command(name="slowmode", description="Set a channel's slowmode")
    @app_commands.describe(seconds="0 turns slowmode off (max 21600 = 6h)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600],
                       channel: discord.TextChannel | None = None):
        channel = channel or interaction.channel
        await channel.edit(slowmode_delay=seconds, reason=audit_reason(interaction.user, "Slowmode"))
        text = f"Slowmode in {channel.mention} set to {seconds}s." if seconds else f"Slowmode off in {channel.mention}."
        await interaction.response.send_message(embed=embeds.success(text))

    @app_commands.command(name="lock", description="Stop @everyone from sending messages in a channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def lock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None,
                   reason: str | None = None):
        channel = channel or interaction.channel
        if not await lock_channel(channel, audit_reason(interaction.user, reason)):
            return await interaction.response.send_message(
                embed=embeds.error(f"{channel.mention} is already locked."), ephemeral=True)
        await interaction.response.send_message(embed=embeds.success(f"🔒 {channel.mention} locked."))
        if channel != interaction.channel:
            await channel.send(embed=embeds.info("🔒 Channel locked", reason))
        await send_log(self.bot, interaction.guild, embeds.info(
            "Channel locked", f"{channel.mention} by {interaction.user.mention}\nReason: {embeds.truncate(reason)}"))

    @app_commands.command(name="unlock", description="Allow @everyone to send messages in a channel again")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def unlock(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        channel = channel or interaction.channel
        await unlock_channel(channel, audit_reason(interaction.user, "Unlock"))
        await interaction.response.send_message(embed=embeds.success(f"🔓 {channel.mention} unlocked."))
        await send_log(self.bot, interaction.guild, embeds.info(
            "Channel unlocked", f"{channel.mention} by {interaction.user.mention}"))

    @app_commands.command(name="lockdown", description="Lock or unlock every text channel in the server")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def lockdown(self, interaction: discord.Interaction, enabled: bool, reason: str | None = None):
        await interaction.response.defer()
        audit = audit_reason(interaction.user, reason or "Server lockdown")
        if enabled:
            count = await lock_server(self.bot, interaction.guild, audit)
            text = f"🔒 Server lockdown started: {count} channel(s) locked."
        else:
            count = await unlock_server(self.bot, interaction.guild, audit)
            text = f"🔓 Server lockdown ended: {count} channel(s) unlocked."
        await interaction.followup.send(embed=embeds.success(text))
        await send_log(self.bot, interaction.guild, embeds.info(
            "Server lockdown", f"{text}\nBy {interaction.user.mention}\nReason: {embeds.truncate(reason)}"))


async def setup(bot):
    await bot.add_cog(Channel(bot))
