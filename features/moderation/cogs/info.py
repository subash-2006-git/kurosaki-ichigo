import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import embeds
from features.moderation.utils.modlog import case_embed, send_log


class Info(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="userinfo", description="Show information about a member")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None):
        member = member or interaction.user
        embed = embeds.info(str(member), guild=interaction.guild)
        if member.color.value:
            embed.colour = member.color
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=f"`{member.id}`")
        embed.add_field(name="Nickname", value=member.nick or "None")
        embed.add_field(name="Bot", value="Yes" if member.bot else "No")
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"))
        if member.joined_at:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "R"))
        if member.is_timed_out():
            embed.add_field(name="Timed out until", value=discord.utils.format_dt(member.timed_out_until, "f"))
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        embed.add_field(name=f"Roles ({len(roles)})", value=embeds.truncate(" ".join(roles) or "None"), inline=False)

        # Staff also see the member's moderation summary.
        if interaction.user.guild_permissions.moderate_members:
            await interaction.response.defer(ephemeral=True)
            warns = await self.bot.mod_db.count_active_warns(interaction.guild_id, member.id)
            cases = await self.bot.mod_db.get_user_cases(interaction.guild_id, member.id, limit=100)
            embed.add_field(name="Moderation", value=f"{warns} active warning(s), {len(cases)} case(s)",
                            inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="serverinfo", description="Show information about this server")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        guild = interaction.guild
        embed = embeds.info(guild.name, guild=guild)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="ID", value=f"`{guild.id}`")
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>")
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, "R"))
        embed.add_field(name="Members", value=str(guild.member_count))
        embed.add_field(name="Channels", value=f"{len(guild.text_channels)} text, {len(guild.voice_channels)} voice")
        embed.add_field(name="Roles", value=str(len(guild.roles)))
        embed.add_field(name="Boosts", value=f"{guild.premium_subscription_count} (tier {guild.premium_tier})")
        embed.add_field(name="Verification", value=str(guild.verification_level).title())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="case", description="Look up a moderation case")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def case(self, interaction: discord.Interaction, number: int):
        await interaction.response.defer(ephemeral=True)
        case = await self.bot.mod_db.get_case(interaction.guild_id, number)
        if case is None:
            return await interaction.followup.send(embed=embeds.error(f"Case #{number} doesn't exist."))
        await interaction.followup.send(embed=case_embed(case))

    @app_commands.command(name="modlogs", description="Show a user's moderation history")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def modlogs(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        cases = await self.bot.mod_db.get_user_cases(interaction.guild_id, user.id, limit=20)
        lines = []
        for c in cases:
            removed = " ~~(removed)~~" if c["action"] == "warn" and not c["active"] else ""
            lines.append(f"**#{c['case_num']}** `{c['action']}`{removed} {discord.utils.format_dt(c['created_at'], 'd')}"
                         f" — {embeds.truncate(c['reason'], 80)}")
        embed = embeds.info(f"Mod history for {user}", embeds.truncate("\n".join(lines) or "No cases.", 4000), interaction.guild)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Showing the latest {len(cases)} case(s)")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="reason", description="Edit the reason of a case")
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def reason(self, interaction: discord.Interaction, case_number: int, reason: str):
        case = await self.bot.mod_db.get_case(interaction.guild_id, case_number)
        if case is None:
            return await interaction.response.send_message(
                embed=embeds.error(f"Case #{case_number} doesn't exist."), ephemeral=True)
        # Only the original moderator or an admin may rewrite history.
        if case["mod_id"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                embed=embeds.error("Only the moderator who made this case or an administrator can edit it."),
                ephemeral=True)
        updated = await self.bot.mod_db.update_case_reason(interaction.guild_id, case_number, reason)
        await interaction.response.send_message(embed=embeds.success(f"Case #{case_number} reason updated."),
                                                ephemeral=True)
        await send_log(self.bot, interaction.guild, case_embed(updated, title_prefix="Updated case"))


async def setup(bot):
    await bot.add_cog(Info(bot))
