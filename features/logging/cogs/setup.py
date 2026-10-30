"""/setup logs and /setup stats: idempotent creation of the bot's channel structure."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from features.logging import settings as config
from features.logging.cogs.stats import compute_stats, find_counters, find_stats_category
from features.logging.services.log_router import add_field, make_embed

if TYPE_CHECKING:
    from core.bot import Bot as LoggerBot

log = logging.getLogger(__name__)

PERMISSION_LABELS = {
    "view_channel": "View Channels",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "attach_files": "Attach Files",
    "read_message_history": "Read Message History",
    "manage_channels": "Manage Channels",
    "view_audit_log": "View Audit Log",
    "connect": "Connect",
    "manage_roles": "Manage Roles",
    "manage_guild": "Manage Server",
}

LOGS_REQUIRED = ("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history", "manage_channels")
STATS_REQUIRED = ("view_channel", "manage_channels", "connect")


def missing_permissions(member: discord.Member, names: tuple[str, ...]) -> list[str]:
    perms = member.guild_permissions
    return [PERMISSION_LABELS[n] for n in names if not getattr(perms, n)]


@dataclass
class SetupReport:
    created: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_embed(self, title: str) -> discord.Embed:
        ok = not self.failed
        embed = make_embed(
            "custom",
            title,
            colour=discord.Colour.green() if ok else discord.Colour.orange(),
        )
        if self.created:
            add_field(embed, f"✅ Created ({len(self.created)})", "\n".join(self.created))
        if self.reused:
            add_field(embed, f"♻️ Reused ({len(self.reused)})", "\n".join(self.reused))
        if self.failed:
            add_field(embed, f"❌ Failed ({len(self.failed)})", "\n".join(self.failed))
        if self.warnings:
            add_field(embed, "⚠️ Warnings", "\n".join(f"• {w}" for w in self.warnings))
        return embed


def _error_text(exc: Exception) -> str:
    if isinstance(exc, discord.Forbidden):
        return "missing permission"
    if isinstance(exc, discord.HTTPException):
        return exc.text or f"HTTP {exc.status}"
    return type(exc).__name__


async def _apply_overwrites(
    category: discord.CategoryChannel,
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite],
    report: SetupReport,
) -> None:
    """Merge the required overwrites into an existing category without removing others."""
    for target, overwrite in overwrites.items():
        if category.overwrites_for(target) == overwrite:
            continue
        try:
            await category.set_permissions(target, overwrite=overwrite, reason="Logger setup")
        except discord.HTTPException as exc:
            report.warnings.append(
                f"Couldn't update permissions for {target.mention} on **{category.name}** ({_error_text(exc)}). "
                "Grant the bot Manage Roles, or adjust the category permissions manually."
            )


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
class Setup(commands.GroupCog, group_name="setup", group_description="Set up the logging channels or server stats."):
    def __init__(self, bot: LoggerBot) -> None:
        self.bot = bot
        super().__init__()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # default_permissions hides the commands, but server admins can override that in
        # Integrations settings, so enforce administrator here as well.
        member = interaction.user
        return isinstance(member, discord.Member) and member.guild_permissions.administrator

    # --- /setup logs -----------------------------------------------------------

    @app_commands.command(name="logs", description="Create the 📂 LOGGING category and its 14 log channels.")
    @app_commands.describe(staff_role="Optional role allowed to read the logs (administrators can always see them).")
    async def logs(self, interaction: discord.Interaction, staff_role: Optional[discord.Role] = None) -> None:
        guild = interaction.guild
        assert guild is not None
        me = guild.me

        missing = missing_permissions(me, LOGS_REQUIRED)
        if missing:
            await interaction.response.send_message(
                f"❌ I can't set up logging yet. Missing permissions: **{', '.join(missing)}**.", ephemeral=True
            )
            return
        if staff_role is not None and (staff_role.is_default() or staff_role.managed):
            await interaction.response.send_message(
                "❌ Choose a regular staff role, not @everyone or a bot/integration role.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        report = SetupReport()
        for perm in ("view_audit_log", "manage_guild"):
            if not getattr(me.guild_permissions, perm):
                effect = (
                    "moderators can't be identified, so actors will be shown as unknown"
                    if perm == "view_audit_log"
                    else "invite usage on joins can't be tracked"
                )
                report.warnings.append(f"Missing **{PERMISSION_LABELS[perm]}**: {effect}.")

        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
            ),
        }
        if staff_role is not None:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=False, add_reactions=False
            )

        category = discord.utils.get(guild.categories, name=config.LOG_CATEGORY_NAME)
        if category is None:
            try:
                category = await guild.create_category(
                    config.LOG_CATEGORY_NAME, overwrites=overwrites, reason=f"/setup logs by {interaction.user}"
                )
                report.created.append(f"📂 **{category.name}** (category)")
            except discord.HTTPException as exc:
                report.failed.append(f"📂 {config.LOG_CATEGORY_NAME} (category): {_error_text(exc)}")
                await interaction.followup.send(embed=report.to_embed("📂 Logging Setup Failed"), ephemeral=True)
                return
        else:
            report.reused.append(f"📂 **{category.name}** (category)")
            await _apply_overwrites(category, overwrites, report)

        for key, (name, topic) in config.LOG_CHANNELS.items():
            existing = next((c for c in category.text_channels if config.log_channel_matches(c.name, key)), None)
            if existing is not None:
                report.reused.append(existing.mention)
                perms = existing.permissions_for(me)
                if not (perms.view_channel and perms.send_messages and perms.embed_links):
                    report.warnings.append(f"I can't post embeds in {existing.mention}; check its permission overrides.")
                continue
            try:
                # No explicit overwrites: the channel syncs with the category's permissions.
                channel = await category.create_text_channel(name, topic=topic, reason="Logger setup")
                report.created.append(channel.mention)
            except discord.HTTPException as exc:
                report.failed.append(f"`{name}`: {_error_text(exc)}")

        self.bot.log_router.invalidate(guild.id)
        access = f"administrators and {staff_role.mention}" if staff_role else "administrators only"
        embed = report.to_embed("📂 Logging Setup Complete" if not report.failed else "📂 Logging Setup Finished With Errors")
        add_field(embed, "👁️ Who can see logs", f"The bot and {access}.")
        await interaction.followup.send(embed=embed, ephemeral=True)

        summary = report.to_embed("🛠️ /setup logs Ran")
        add_field(summary, "Run by", interaction.user.mention)
        self.bot.log_router.send(guild, "custom", summary)

    # --- /setup stats ----------------------------------------------------------

    @app_commands.command(name="stats", description="Create the 📊 SERVER STATS category and its five live counters.")
    async def stats(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        me = guild.me

        missing = missing_permissions(me, STATS_REQUIRED)
        if missing:
            await interaction.response.send_message(
                f"❌ I can't set up stats yet. Missing permissions: **{', '.join(missing)}**.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        report = SetupReport()
        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
            me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
        }

        category = find_stats_category(guild)
        if category is None:
            try:
                category = await guild.create_category(
                    config.STATS_CATEGORY_NAME,
                    overwrites=overwrites,
                    position=0,
                    reason=f"/setup stats by {interaction.user}",
                )
                report.created.append(f"📊 **{category.name}** (category)")
            except discord.HTTPException as exc:
                report.failed.append(f"📊 {config.STATS_CATEGORY_NAME} (category): {_error_text(exc)}")
                await interaction.followup.send(embed=report.to_embed("📊 Stats Setup Failed"), ephemeral=True)
                return
        else:
            report.reused.append(f"📊 **{category.name}** (category)")
            await _apply_overwrites(category, overwrites, report)

        values = compute_stats(guild)
        existing = find_counters(guild)
        for key in config.STATS_COUNTERS:
            if key in existing:
                report.reused.append(existing[key].mention)
                continue
            name = config.stats_channel_name(key, values[key])
            try:
                channel = await category.create_voice_channel(name, reason="Logger stats setup")
                report.created.append(channel.mention)
            except discord.HTTPException as exc:
                report.failed.append(f"`{name}`: {_error_text(exc)}")

        if not guild.chunked:
            report.warnings.append("Member list is still loading; counts will correct themselves shortly.")

        stats_cog = self.bot.get_cog("Stats")
        if stats_cog is not None:
            stats_cog.mark_dirty(guild)  # type: ignore[attr-defined]

        embed = report.to_embed("📊 Stats Setup Complete" if not report.failed else "📊 Stats Setup Finished With Errors")
        add_field(
            embed,
            "🔄 Updates",
            f"Counters refresh after relevant events (checked every {config.STATS_UPDATE_INTERVAL}s) and are fully "
            f"reconciled every {config.STATS_RECONCILE_INTERVAL // 60} min. Discord limits each channel to about "
            "2 renames per 10 minutes, so busy counters may lag briefly.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        summary = report.to_embed("🛠️ /setup stats Ran")
        add_field(summary, "Run by", interaction.user.mention)
        self.bot.log_router.send(guild, "custom", summary)


async def setup(bot: LoggerBot) -> None:
    await bot.add_cog(Setup(bot))
