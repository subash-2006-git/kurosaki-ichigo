import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import embeds
from features.moderation.utils.actions import audit_reason
from features.moderation.utils.checks import role_error
from features.moderation.utils.modlog import send_log
from features.moderation.utils.time import humanize, parse_duration


class Roles(commands.Cog):
    role = app_commands.Group(
        name="role",
        description="Manage member roles",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_roles:
            raise app_commands.MissingPermissions(["manage_roles"])
        if not interaction.guild.me.guild_permissions.manage_roles:
            raise app_commands.BotMissingPermissions(["manage_roles"])
        return True

    async def _log(self, interaction: discord.Interaction, text: str) -> None:
        await send_log(self.bot, interaction.guild, embeds.info("Role change", f"{text}\nBy {interaction.user.mention}"))

    @role.command(name="add", description="Give a member a role")
    async def add(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if err := role_error(interaction.user, role):
            return await interaction.response.send_message(embed=embeds.error(err), ephemeral=True)
        if role in member.roles:
            return await interaction.response.send_message(
                embed=embeds.error(f"{member.mention} already has {role.mention}."), ephemeral=True)
        await member.add_roles(role, reason=audit_reason(interaction.user, "Role add"))
        await interaction.response.send_message(embed=embeds.success(f"Gave {role.mention} to {member.mention}."))
        await self._log(interaction, f"{role.mention} added to {member.mention}")

    @role.command(name="remove", description="Remove a role from a member")
    async def remove(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if err := role_error(interaction.user, role):
            return await interaction.response.send_message(embed=embeds.error(err), ephemeral=True)
        if role not in member.roles:
            return await interaction.response.send_message(
                embed=embeds.error(f"{member.mention} doesn't have {role.mention}."), ephemeral=True)
        await member.remove_roles(role, reason=audit_reason(interaction.user, "Role remove"))
        await self.bot.mod_db.clear_scheduled(interaction.guild_id, member.id, "remove_role", role.id)
        await interaction.response.send_message(embed=embeds.success(f"Removed {role.mention} from {member.mention}."))
        await self._log(interaction, f"{role.mention} removed from {member.mention}")

    @role.command(name="temp", description="Give a member a role for a limited time")
    @app_commands.describe(duration="e.g. 1h, 3d, 2w")
    async def temp(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role, duration: str):
        delta = parse_duration(duration)
        if delta is None:
            return await interaction.response.send_message(
                embed=embeds.error("Invalid duration. Use formats like `30m`, `2h`, `7d`."), ephemeral=True)
        if err := role_error(interaction.user, role):
            return await interaction.response.send_message(embed=embeds.error(err), ephemeral=True)
        await interaction.response.defer()
        if role not in member.roles:
            await member.add_roles(role, reason=audit_reason(interaction.user, f"Temp role for {humanize(delta.total_seconds())}"))
        await self.bot.mod_db.clear_scheduled(interaction.guild_id, member.id, "remove_role", role.id)
        await self.bot.mod_db.add_scheduled(interaction.guild_id, member.id, "remove_role",
                                        discord.utils.utcnow() + delta, role_id=role.id)
        text = f"{role.mention} given to {member.mention} for {humanize(delta.total_seconds())}"
        await interaction.followup.send(embed=embeds.success(text + "."))
        await self._log(interaction, text)


async def setup(bot):
    await bot.add_cog(Roles(bot))
