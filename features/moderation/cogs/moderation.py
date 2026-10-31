import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import actions, embeds
from features.moderation.utils.checks import hierarchy_error
from features.moderation.utils.time import MAX_TIMEOUT, humanize, parse_duration


def _reason():
    # A fresh decorator per command: describe() stores its dict on the function, so sharing one would leak.
    return app_commands.describe(reason="Shown in the mod-log, audit log and the user's DM")


async def _reject(interaction: discord.Interaction, message: str) -> None:
    await interaction.response.send_message(embed=embeds.error(message), ephemeral=True)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="ban", description="Ban a user (they don't need to be in the server)")
    @app_commands.describe(user="User or user ID", delete_days="Delete their messages from the last N days")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User,
                  reason: str | None = None, delete_days: app_commands.Range[int, 0, 7] = 0):
        if err := hierarchy_error(interaction.user, user):
            return await _reject(interaction, err)
        await interaction.response.defer()
        case = await actions.ban(self.bot, interaction.guild, user, interaction.user, reason,
                                 delete_message_days=delete_days)
        await interaction.followup.send(embed=embeds.success(
            f"**{user}** was banned. (Case #{case['case_num']})"))

    @app_commands.command(name="tempban", description="Ban a user for a limited time")
    @app_commands.describe(user="User or user ID", duration="e.g. 1h, 3d, 2w",
                           delete_days="Delete their messages from the last N days")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def tempban(self, interaction: discord.Interaction, user: discord.User, duration: str,
                      reason: str | None = None, delete_days: app_commands.Range[int, 0, 7] = 0):
        delta = parse_duration(duration)
        if delta is None:
            return await _reject(interaction, "Invalid duration. Use formats like `30m`, `2h`, `7d`.")
        if err := hierarchy_error(interaction.user, user):
            return await _reject(interaction, err)
        await interaction.response.defer()
        case = await actions.ban(self.bot, interaction.guild, user, interaction.user, reason,
                                 duration=delta, delete_message_days=delete_days)
        await interaction.followup.send(embed=embeds.success(
            f"**{user}** was banned for {humanize(delta.total_seconds())}. (Case #{case['case_num']})"))

    @app_commands.command(name="softban", description="Ban and immediately unban to wipe recent messages")
    @app_commands.describe(delete_days="Delete their messages from the last N days")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def softban(self, interaction: discord.Interaction, member: discord.Member,
                      reason: str | None = None, delete_days: app_commands.Range[int, 1, 7] = 1):
        if err := hierarchy_error(interaction.user, member):
            return await _reject(interaction, err)
        await interaction.response.defer()
        case = await actions.softban(self.bot, interaction.guild, member, interaction.user, reason, delete_days)
        await interaction.followup.send(embed=embeds.success(
            f"**{member}** was softbanned. (Case #{case['case_num']})"))

    @app_commands.command(name="unban", description="Unban a user by ID")
    @app_commands.describe(user_id="The banned user's ID")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str | None = None):
        if not user_id.strip().isdigit():
            return await _reject(interaction, "That isn't a valid user ID.")
        await interaction.response.defer()
        try:
            ban_entry = await interaction.guild.fetch_ban(discord.Object(int(user_id)))
        except discord.NotFound:
            return await interaction.followup.send(embed=embeds.error("That user isn't banned."))
        case = await actions.unban(self.bot, interaction.guild, ban_entry.user, interaction.user, reason)
        await interaction.followup.send(embed=embeds.success(
            f"**{ban_entry.user}** was unbanned. (Case #{case['case_num']})"))

    @unban.autocomplete("user_id")
    async def unban_autocomplete(self, interaction: discord.Interaction, current: str):
        if not interaction.guild.me.guild_permissions.ban_members:
            return []
        current = current.lower()
        choices = []
        async for entry in interaction.guild.bans(limit=500):
            label = f"{entry.user} ({entry.user.id})"
            if current in label.lower():
                choices.append(app_commands.Choice(name=label[:100], value=str(entry.user.id)))
            if len(choices) >= 25:
                break
        return choices

    @app_commands.command(name="kick", description="Kick a member from the server")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None):
        if err := hierarchy_error(interaction.user, member):
            return await _reject(interaction, err)
        await interaction.response.defer()
        case = await actions.kick(self.bot, interaction.guild, member, interaction.user, reason)
        await interaction.followup.send(embed=embeds.success(
            f"**{member}** was kicked. (Case #{case['case_num']})"))

    @app_commands.command(name="timeout", description="Time out a member (max 28 days)")
    @app_commands.describe(duration="e.g. 10m, 1h, 1d")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, duration: str,
                      reason: str | None = None):
        delta = parse_duration(duration)
        if delta is None:
            return await _reject(interaction, "Invalid duration. Use formats like `10m`, `1h`, `1d`.")
        if delta > MAX_TIMEOUT:
            return await _reject(interaction, "Timeouts can be at most 28 days. Use /tempban for longer.")
        if member.guild_permissions.administrator:
            return await _reject(interaction, "Administrators can't be timed out.")
        if err := hierarchy_error(interaction.user, member):
            return await _reject(interaction, err)
        await interaction.response.defer()
        case = await actions.timeout(self.bot, interaction.guild, member, interaction.user, reason, delta)
        await interaction.followup.send(embed=embeds.success(
            f"**{member}** was timed out for {humanize(delta.total_seconds())}. (Case #{case['case_num']})"))

    @app_commands.command(name="untimeout", description="Remove a member's timeout")
    @_reason()
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: str | None = None):
        if not member.is_timed_out():
            return await _reject(interaction, f"{member.mention} isn't timed out.")
        if err := hierarchy_error(interaction.user, member):
            return await _reject(interaction, err)
        await interaction.response.defer()
        case = await actions.untimeout(self.bot, interaction.guild, member, interaction.user, reason)
        await interaction.followup.send(embed=embeds.success(
            f"**{member}**'s timeout was removed. (Case #{case['case_num']})"))


async def setup(bot):
    await bot.add_cog(Moderation(bot))
