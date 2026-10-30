import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import actions, embeds
from features.moderation.utils.checks import hierarchy_error
from features.moderation.utils.modlog import send_log

class Warnings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.describe(reason="Shown in the mod-log and the user's DM")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if member.bot:
            return await interaction.response.send_message(embed=embeds.error("You can't warn bots."),
                                                           ephemeral=True)
        if err := hierarchy_error(interaction.user, member):
            return await interaction.response.send_message(embed=embeds.error(err), ephemeral=True)
        await interaction.response.defer()
        case, escalation = await actions.warn(self.bot, interaction.guild, member, interaction.user, reason)
        count = await self.bot.mod_db.count_active_warns(interaction.guild_id, member.id)
        message = f"**{member}** was warned. They now have **{count}** warning(s). (Case #{case['case_num']})"
        if escalation:
            message += f"\n⚠️ Auto-escalation: **{escalation['action']}** (Case #{escalation['case_num']})"
        await interaction.followup.send(embed=embeds.success(message))

    @app_commands.command(name="warnings", description="List a member's active warnings")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer()
        warns = await self.bot.mod_db.get_active_warns(interaction.guild_id, user.id)
        embed = embeds.info(f"Warnings for {user}", None if warns else "No active warnings.")
        embed.set_thumbnail(url=user.display_avatar.url)
        for w in warns[-25:]:
            embed.add_field(
                name=f"Case #{w['case_num']} • {discord.utils.format_dt(w['created_at'], 'R')}",
                value=f"{embeds.truncate(w['reason'], 900)}\nby <@{w['mod_id']}>",
                inline=False,
            )
        embed.set_footer(text=f"{len(warns)} active warning(s)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="delwarn", description="Remove a single warning by case number")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def delwarn(self, interaction: discord.Interaction, case_number: int, reason: str | None = None):
        case = await self.bot.mod_db.deactivate_warn(interaction.guild_id, case_number)
        if case is None:
            return await interaction.response.send_message(
                embed=embeds.error(f"Case #{case_number} isn't an active warning."), ephemeral=True)
        await interaction.response.send_message(embed=embeds.success(f"Warning #{case_number} removed."))
        log_embed = embeds.info("Warning removed",
                                f"Case #{case_number} for <@{case['user_id']}> removed by {interaction.user.mention}"
                                f"\nReason: {embeds.truncate(reason)}")
        await send_log(self.bot, interaction.guild, log_embed)

    @app_commands.command(name="clearwarns", description="Remove all of a member's active warnings")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarns(self, interaction: discord.Interaction, user: discord.User, reason: str | None = None):
        count = await self.bot.mod_db.clear_warns(interaction.guild_id, user.id)
        if not count:
            return await interaction.response.send_message(
                embed=embeds.error(f"{user.mention} has no active warnings."), ephemeral=True)
        await interaction.response.send_message(embed=embeds.success(f"Cleared {count} warning(s) for **{user}**."))
        log_embed = embeds.info("Warnings cleared",
                                f"{count} warning(s) for {user.mention} cleared by {interaction.user.mention}"
                                f"\nReason: {embeds.truncate(reason)}")
        await send_log(self.bot, interaction.guild, log_embed)


async def setup(bot):
    await bot.add_cog(Warnings(bot))
