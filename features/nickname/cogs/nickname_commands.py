from collections import Counter
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown

from features.nickname.db import GuildSettings
from features.nickname.services.job_manager import format_counter, job_embed
from features.nickname.services.nickname_planner import PLANNERS, Decision, account_name
from features.nickname.services.nickname_templates import TemplateError, render, validate_template
from features.nickname.services.permission_checks import bot_permission_problem

if TYPE_CHECKING:
    from core.bot import Bot as NicknameBot

MODE_LABELS = {
    "off": "Off: no automatic nickname changes",
    "new": "New members: rename members when they join",
    "existing": "Existing members: allow a one-time bulk rename",
    "both": "Both: bulk rename and rename future members",
}
PREVIEW_EXAMPLES = 10
CONFIRM_TIMEOUT = 120

Change = tuple[discord.Member, Decision]


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, confirm_label: str):
        super().__init__(timeout=CONFIRM_TIMEOUT)
        self.author_id = author_id
        self.confirmed: bool | None = None
        self.interaction: discord.Interaction | None = None
        self.confirm.label = confirm_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the administrator who ran this command can confirm it.", ephemeral=True
            )
            return False
        return True

    async def _finish(self, interaction: discord.Interaction, confirmed: bool) -> None:
        self.confirmed = confirmed
        self.interaction = interaction
        self.stop()

    @discord.ui.button(style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._finish(interaction, False)


def describe_change(member: discord.Member, decision: Decision) -> str:
    old = escape_markdown(member.display_name)
    if decision.new_nick is None:
        new = f"{escape_markdown(account_name(member))} *(nickname removed)*"
    else:
        new = escape_markdown(decision.new_nick)
    return f"{old} → **{new}**"


def plan_embed(title: str, changes: list[Change], skipped: Counter) -> discord.Embed:
    embed = discord.Embed(title=title, colour=discord.Colour.blurple())
    embed.add_field(name="Will change", value=f"{len(changes)} members")
    embed.add_field(name="Will skip", value=f"{sum(skipped.values())} members")
    if changes:
        lines = [describe_change(m, d) for m, d in changes[:PREVIEW_EXAMPLES]]
        if len(changes) > PREVIEW_EXAMPLES:
            lines.append(f"…and {len(changes) - PREVIEW_EXAMPLES} more")
        embed.add_field(name="Examples", value="\n".join(lines)[:1024], inline=False)
    embed.add_field(name="Skip reasons", value=format_counter(skipped), inline=False)
    return embed


def settings_embed(guild: discord.Guild, settings: GuildSettings) -> discord.Embed:
    embed = discord.Embed(title="Nickname Manager settings", colour=discord.Colour.blurple())
    embed.add_field(name="Mode", value=MODE_LABELS[settings.mode], inline=False)
    embed.add_field(
        name="Template",
        value=f"`{settings.template}`" if settings.template else "Not set. Use `/nick template`.",
        inline=False,
    )
    embed.add_field(name="Include bots", value="Yes" if settings.include_bots else "No")
    roles = [f"<@&{r}>" for r in sorted(settings.excluded_roles)]
    members = [f"<@{m}>" for m in sorted(settings.excluded_members)]
    embed.add_field(name="Excluded roles", value=", ".join(roles)[:1024] or "None", inline=False)
    embed.add_field(name="Excluded members", value=", ".join(members)[:1024] or "None", inline=False)
    if problem := bot_permission_problem(guild):
        embed.add_field(name="⚠ Problem", value=problem, inline=False)
    return embed


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class NicknameCommands(commands.GroupCog, group_name="nick", group_description="Manage member nicknames"):
    def __init__(self, bot: "NicknameBot"):
        self.bot = bot
        self.db = bot.nick_db
        super().__init__()

    # --- configuration --------------------------------------------------------

    @app_commands.command(name="mode", description="Choose when nicknames are changed")
    @app_commands.choices(mode=[app_commands.Choice(name=label, value=key) for key, label in MODE_LABELS.items()])
    async def mode(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        settings = await self.db.get_settings(interaction.guild_id)
        settings.mode = mode.value
        await self.db.save_settings(settings)

        # Changing the mode never starts a bulk job on its own.
        message = f"Mode set to **{mode.name}**."
        if settings.allows_bulk:
            message += "\nNo one has been renamed yet. Run `/nick preview`, then `/nick apply` to rename existing members."
        if settings.mode != "off" and not settings.template:
            message += "\n⚠ No template is set yet. Use `/nick template` first."
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="template", description="Set the nickname format, e.g. ★ {name}")
    @app_commands.describe(format="Use {name} for the member's name and {username} for their account username")
    async def template(self, interaction: discord.Interaction, format: app_commands.Range[str, 1, 100]):
        try:
            template = validate_template(format)
            example = render(template, account_name(interaction.user), interaction.user.name)
        except TemplateError as exc:
            await interaction.response.send_message(f"That template won't work: {exc}", ephemeral=True)
            return

        settings = await self.db.get_settings(interaction.guild_id)
        settings.template = template
        await self.db.save_settings(settings)
        await interaction.response.send_message(
            f"Template set to `{template}`.\n"
            f"Your nickname would look like this: **{escape_markdown(example)}**\n"
            "Existing nicknames haven't changed. Run `/nick apply` to update them.",
            ephemeral=True,
        )

    @app_commands.command(name="settings", description="Show the current nickname settings")
    async def settings(self, interaction: discord.Interaction):
        settings = await self.db.get_settings(interaction.guild_id)
        await interaction.response.send_message(embed=settings_embed(interaction.guild, settings), ephemeral=True)

    @app_commands.command(name="exclude-role", description="Never rename members with this role")
    @app_commands.choices(action=[app_commands.Choice(name="Add", value="add"), app_commands.Choice(name="Remove", value="remove")])
    async def exclude_role(self, interaction: discord.Interaction, action: app_commands.Choice[str], role: discord.Role):
        settings = await self.db.get_settings(interaction.guild_id)
        if action.value == "add":
            settings.excluded_roles.add(role.id)
            message = f"Members with {role.mention} will be skipped."
        else:
            settings.excluded_roles.discard(role.id)
            message = f"{role.mention} is no longer excluded."
        await self.db.save_settings(settings)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="exclude-member", description="Never rename this member")
    @app_commands.choices(action=[app_commands.Choice(name="Add", value="add"), app_commands.Choice(name="Remove", value="remove")])
    async def exclude_member(self, interaction: discord.Interaction, action: app_commands.Choice[str], member: discord.Member):
        settings = await self.db.get_settings(interaction.guild_id)
        if action.value == "add":
            settings.excluded_members.add(member.id)
            message = f"{member.mention} will be skipped."
        else:
            settings.excluded_members.discard(member.id)
            message = f"{member.mention} is no longer excluded."
        await self.db.save_settings(settings)
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="include-bots", description="Choose whether bot accounts are renamed")
    async def include_bots(self, interaction: discord.Interaction, enabled: bool):
        settings = await self.db.get_settings(interaction.guild_id)
        settings.include_bots = enabled
        await self.db.save_settings(settings)
        state = "will be renamed" if enabled else "will be skipped"
        await interaction.response.send_message(f"Bot accounts {state}.", ephemeral=True)

    # --- bulk operations ------------------------------------------------------

    async def _build_plan(self, guild: discord.Guild, kind: str) -> tuple[list[Change], Counter]:
        if not guild.chunked:
            await guild.chunk()
        settings = await self.db.get_settings(guild.id)
        records = await self.db.get_all_records(guild.id)
        planner = PLANNERS[kind]

        changes: list[Change] = []
        skipped: Counter = Counter()
        for member in guild.members:
            decision = planner(member, settings, records.get(member.id))
            if decision.skipped:
                skipped[decision.skip_reason] += 1
            else:
                changes.append((member, decision))
        return changes, skipped

    async def _blocker(self, guild: discord.Guild, kind: str) -> str | None:
        """Return why a bulk job can't start right now, if anything stops it."""
        if problem := bot_permission_problem(guild):
            return problem
        if job := self.bot.nick_jobs.current(guild.id):
            return f"Job #{job.id} is still running. Check `/nick status` or use `/nick stop`."
        if kind == "apply":
            settings = await self.db.get_settings(guild.id)
            if not settings.template:
                return "Set a nickname format first with `/nick template`."
            if not settings.allows_bulk:
                return "Bulk renaming is turned off. Set `/nick mode` to **Existing members** or **Both** first."
        return None

    @app_commands.command(name="preview", description="Preview nickname changes without applying them")
    async def preview(self, interaction: discord.Interaction):
        settings = await self.db.get_settings(interaction.guild_id)
        if not settings.template:
            await interaction.response.send_message("Set a nickname format first with `/nick template`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        changes, skipped = await self._build_plan(interaction.guild, "apply")
        embed = plan_embed(f"Preview: {settings.template}", changes, skipped)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _confirm_and_start(self, interaction: discord.Interaction, kind: str) -> None:
        if blocker := await self._blocker(interaction.guild, kind):
            await interaction.response.send_message(blocker, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        changes, skipped = await self._build_plan(interaction.guild, kind)
        if not changes:
            await interaction.followup.send(
                embed=plan_embed("Nothing to change", changes, skipped), ephemeral=True
            )
            return

        verb = "Rename" if kind == "apply" else "Reset"
        view = ConfirmView(interaction.user.id, f"{verb} {len(changes)} members")
        message = await interaction.followup.send(
            content="Review the changes below. They're applied gradually and can be stopped with `/nick stop`.",
            embed=plan_embed(f"Confirm {verb.lower()}", changes, skipped),
            view=view,
            ephemeral=True,
            wait=True,
        )

        if await view.wait() or view.interaction is None:
            await message.edit(content="Confirmation timed out. Nothing was changed.", embed=None, view=None)
            return
        if not view.confirmed:
            await view.interaction.response.edit_message(content="Cancelled. Nothing was changed.", embed=None, view=None)
            return

        # Check again: something may have changed while the admin was deciding.
        if blocker := await self._blocker(interaction.guild, kind):
            await view.interaction.response.edit_message(content=blocker, embed=None, view=None)
            return

        job = await self.bot.nick_jobs.start(
            interaction.guild,
            kind,
            [member.id for member, _ in changes],
            started_by=interaction.user.id,
            channel_id=interaction.channel_id,
        )
        await view.interaction.response.edit_message(
            content=f"Started job #{job.id} for {job.total} members. "
            "Follow it with `/nick status`. A report will be posted in this channel when it finishes.",
            embed=None,
            view=None,
        )

    @app_commands.command(name="apply", description="Rename existing members using the template")
    async def apply(self, interaction: discord.Interaction):
        await self._confirm_and_start(interaction, "apply")

    @app_commands.command(name="reset", description="Restore nicknames the bot changed to what they were before")
    async def reset(self, interaction: discord.Interaction):
        await self._confirm_and_start(interaction, "reset")

    @app_commands.command(name="status", description="Show progress of the current or most recent job")
    async def status(self, interaction: discord.Interaction):
        job = await self.bot.nick_jobs.latest(interaction.guild_id)
        if job is None:
            await interaction.response.send_message("No bulk jobs have run in this server yet.", ephemeral=True)
            return
        await interaction.response.send_message(embed=job_embed(job), ephemeral=True)

    @app_commands.command(name="stop", description="Stop the running bulk job after the current member")
    async def stop(self, interaction: discord.Interaction):
        job = self.bot.nick_jobs.stop(interaction.guild_id)
        if job is None:
            await interaction.response.send_message("No job is running.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Stopping job #{job.id} after the current member. "
            f"{job.changed} nicknames were changed so far.",
            ephemeral=True,
        )


async def setup(bot: "NicknameBot"):
    await bot.add_cog(NicknameCommands(bot))
