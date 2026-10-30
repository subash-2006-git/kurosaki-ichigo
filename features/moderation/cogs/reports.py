import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import actions, embeds
from features.moderation.utils.checks import hierarchy_error

log = logging.getLogger(__name__)

# action -> (label, style, permission the clicking moderator needs)
REPORT_ACTIONS = {
    "warn": ("Warn", discord.ButtonStyle.secondary, "moderate_members"),
    "timeout": ("Timeout 1h", discord.ButtonStyle.primary, "moderate_members"),
    "kick": ("Kick", discord.ButtonStyle.danger, "kick_members"),
    "ban": ("Ban", discord.ButtonStyle.danger, "ban_members"),
    "dismiss": ("Dismiss", discord.ButtonStyle.success, "moderate_members"),
}


class ReportButton(discord.ui.DynamicItem[discord.ui.Button], template=r"report:(?P<action>[a-z]+):(?P<id>\d+)"):
    """Persistent button: the action and report id live in the custom_id, so it survives restarts."""

    def __init__(self, action: str, report_id: int):
        label, style, _ = REPORT_ACTIONS[action]
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"report:{action}:{report_id}"))
        self.action = action
        self.report_id = report_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        action = match["action"]
        if action not in REPORT_ACTIONS:
            raise ValueError(f"Unknown report action {action}")
        return cls(action, int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        bot = interaction.client
        guild = interaction.guild
        moderator = interaction.user
        permission = REPORT_ACTIONS[self.action][2]
        if not getattr(moderator.guild_permissions, permission):
            return await interaction.response.send_message(
                embed=embeds.error(f"You need **{permission.replace('_', ' ').title()}** to do that."), ephemeral=True)

        report = await bot.mod_db.get_report(guild.id, self.report_id)
        if report is None or report["status"] != "open":
            return await interaction.response.send_message(
                embed=embeds.error("This report has already been handled."), ephemeral=True)

        target = guild.get_member(report["target_id"])
        if self.action != "dismiss":
            if target is None and self.action != "ban":
                return await interaction.response.send_message(
                    embed=embeds.error("That member has left the server."), ephemeral=True)
            if target is not None and (err := hierarchy_error(moderator, target)):
                return await interaction.response.send_message(embed=embeds.error(err), ephemeral=True)

        # Claim the report first so two moderators can't act on it at once.
        if not await bot.mod_db.close_report(self.report_id, self.action, moderator.id):
            return await interaction.response.send_message(
                embed=embeds.error("Another moderator just handled this report."), ephemeral=True)
        await interaction.response.defer()

        reason = f"Report #{self.report_id}: {report['reason']}"
        try:
            match self.action:
                case "warn":
                    await actions.warn(bot, guild, target, moderator, reason)
                case "timeout":
                    await actions.timeout(bot, guild, target, moderator, reason, timedelta(hours=1))
                case "kick":
                    await actions.kick(bot, guild, target, moderator, reason)
                case "ban":
                    user = target or await bot.fetch_user(report["target_id"])
                    await actions.ban(bot, guild, user, moderator, reason)
        except discord.HTTPException as exc:
            await bot.mod_db.reopen_report(self.report_id)
            return await interaction.followup.send(embed=embeds.error(f"Action failed: {exc.text}"), ephemeral=True)

        embed = interaction.message.embeds[0] if interaction.message.embeds else embeds.info("Report")
        verb = "Dismissed" if self.action == "dismiss" else f"Handled ({REPORT_ACTIONS[self.action][0]})"
        embed.color = discord.Color.green() if self.action == "dismiss" else discord.Color.dark_grey()
        embed.add_field(name="Status", value=f"{verb} by {moderator.mention}", inline=False)
        await interaction.message.edit(embed=embed, view=None)


def report_view(report_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in REPORT_ACTIONS:
        view.add_item(ReportButton(action, report_id))
    return view


class ReportModal(discord.ui.Modal, title="Report"):
    reason = discord.ui.TextInput(label="What's the problem?", style=discord.TextStyle.paragraph,
                                  max_length=500)

    def __init__(self, cog: "Reports", target: discord.abc.User, message: discord.Message | None = None):
        super().__init__(title=f"Report {target}"[:45])
        self.cog = cog
        self.target = target
        self.message = message

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.submit(interaction, self.target, self.reason.value, self.message)


class Reports(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.menus = [
            app_commands.ContextMenu(name="Report Message", callback=self.report_message_menu),
            app_commands.ContextMenu(name="Report User", callback=self.report_user_menu),
        ]
        for menu in self.menus:
            menu.guild_only = True
            self.bot.tree.add_command(menu)

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(ReportButton)

    async def cog_unload(self) -> None:
        for menu in self.menus:
            self.bot.tree.remove_command(menu.name, type=menu.type)
        self.bot.remove_dynamic_items(ReportButton)

    async def submit(self, interaction: discord.Interaction, target: discord.abc.User, reason: str,
                     message: discord.Message | None = None) -> None:
        if target.bot or target.id == interaction.user.id:
            return await interaction.response.send_message(
                embed=embeds.error("You can't report yourself or a bot."), ephemeral=True)
        settings = await self.bot.mod_configs.get(interaction.guild_id)
        channel = interaction.guild.get_channel(settings.report_channel_id) if settings.report_channel_id else None
        if channel is None:
            return await interaction.response.send_message(
                embed=embeds.error("Reports aren't set up on this server. Please contact a moderator directly."),
                ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        report = await self.bot.mod_db.create_report(interaction.guild_id, interaction.user.id, target.id, reason,
                                                 message.jump_url if message else None)

        embed = discord.Embed(title=f"Report #{report['id']}", color=discord.Color.orange(),
                              timestamp=report["created_at"])
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Reported user", value=f"{target.mention} (`{target.id}`)")
        embed.add_field(name="Reported by", value=interaction.user.mention)
        embed.add_field(name="Reason", value=embeds.truncate(reason), inline=False)
        if message:
            embed.add_field(name="Message", value=f"{embeds.truncate(message.content or '*no text*', 900)}\n"
                                                  f"[Jump to message]({message.jump_url})", inline=False)
        warns = await self.bot.mod_db.count_active_warns(interaction.guild_id, target.id)
        embed.set_footer(text=f"User has {warns} active warning(s)")

        await channel.send(embed=embed, view=report_view(report["id"]),
                           allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(embed=embeds.success("Thanks, your report was sent to the moderators."))

    @app_commands.command(name="report", description="Report a member to the moderators")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60, key=lambda i: (i.guild_id, i.user.id))
    async def report(self, interaction: discord.Interaction, member: discord.Member,
                     reason: app_commands.Range[str, 1, 500]):
        await self.submit(interaction, member, reason)

    @app_commands.checks.cooldown(1, 60, key=lambda i: (i.guild_id, i.user.id))
    async def report_message_menu(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.send_modal(ReportModal(self, message.author, message))

    @app_commands.checks.cooldown(1, 60, key=lambda i: (i.guild_id, i.user.id))
    async def report_user_menu(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.send_modal(ReportModal(self, member))


async def setup(bot):
    await bot.add_cog(Reports(bot))
