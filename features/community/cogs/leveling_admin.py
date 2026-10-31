"""/leveling: every leveling setting, level roles, and admin XP tools (Manage Server by default)."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.bot import Bot as CommunityBot
from core.interactions import DEFER, defer, reply
from core.ui.theme import brand
from features.community.models import RoleMode
from features.community.repositories import LevelRoleRepo
from features.community.services.guild_config import GuildConfig
from features.community.services.leveling import AwardResult
from features.community.services.roles import Milestone
from features.community.utils.checks import channel_problem_reply, resolve_channel, role_problem
from features.community.utils.placeholders import render
from features.community.utils.views import ConfirmView, TemplateModal

log = logging.getLogger(__name__)

MAX_LEVELUP_TEXT = 500
MAX_CONTENT = 2000
MAX_XP = 100_000_000
ROLE_MODE_CHOICES = [
    app_commands.Choice(name="Highest only (remove lower level roles)", value=RoleMode.HIGHEST.value),
    app_commands.Choice(name="Keep all reached level roles", value=RoleMode.KEEP_ALL.value),
]


def on_off(value: bool) -> str:
    return "On" if value else "Off"


def seconds(value: int) -> str:
    if value and value % 60 == 0:
        minutes = value // 60
        return f"{minutes} min" if minutes != 1 else "1 min"
    return f"{value}s"


class LevelingAdminCog(commands.Cog):
    leveling = app_commands.Group(
        name="leveling",
        description="Configure the leveling system",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    role = app_commands.Group(name="role", description="Roles given at level milestones", parent=leveling)
    xp = app_commands.Group(name="xp", description="Change members' XP", parent=leveling)

    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ on/off and overview

    @leveling.command(name="enable", description="Start awarding XP for messages and voice.")
    async def enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.update(interaction.guild.id, leveling_enabled=True)
        await reply(interaction,
            "✅ Leveling enabled. Members earn "
            f"{config.message_xp_min}–{config.message_xp_max} XP per message (at most once every "
            f"{seconds(config.message_xp_cooldown)}) and {config.voice_xp_amount} XP every "
            f"{seconds(config.voice_xp_interval)} in voice. Check progress with `/rank`; "
            "see everything with `/leveling settings`.",
            ephemeral=True,
        )

    @leveling.command(name="disable", description="Stop awarding XP. Existing XP is kept.")
    async def disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, leveling_enabled=False)
        await reply(interaction, "✅ Leveling disabled. Everyone's XP is kept.", ephemeral=True)

    @leveling.command(name="settings", description="Show all leveling settings.")
    async def settings(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)
        milestones = await self.bot.roles.milestones(interaction.guild.id)
        await reply(interaction,
            embed=settings_embed(interaction.guild, config, milestones), ephemeral=True
        )

    # ------------------------------------------------------------------ message XP

    @leveling.command(name="message-xp", description="Random XP per message, between min and max.")
    @app_commands.rename(minimum="min", maximum="max")
    async def message_xp(
        self,
        interaction: discord.Interaction,
        minimum: app_commands.Range[int, 1, 100],
        maximum: app_commands.Range[int, 1, 100],
    ) -> None:
        assert interaction.guild is not None
        if minimum > maximum:
            await reply(interaction, "❌ min can't be more than max.", ephemeral=True)
            return
        await self.bot.guild_config.update(interaction.guild.id, message_xp_min=minimum, message_xp_max=maximum)
        amount = f"{minimum}" if minimum == maximum else f"{minimum}–{maximum}"
        await reply(interaction, f"✅ Messages now earn {amount} XP.", ephemeral=True)

    @leveling.command(name="message-cooldown", description="Seconds a member must wait between XP-earning messages.")
    @app_commands.rename(seconds_="seconds")
    async def message_cooldown(
        self, interaction: discord.Interaction, seconds_: app_commands.Range[int, 0, 3600]
    ) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, message_xp_cooldown=seconds_)
        note = "every message earns XP" if seconds_ == 0 else f"at most one XP-earning message every {seconds(seconds_)}"
        await reply(interaction, f"✅ Message cooldown set: {note}.", ephemeral=True)

    # ------------------------------------------------------------------ voice XP

    @leveling.command(name="voice-xp", description="XP given each voice interval.")
    async def voice_xp(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.update(interaction.guild.id, voice_xp_amount=amount)
        await reply(interaction,
            f"✅ Voice now earns {amount} XP every {seconds(config.voice_xp_interval)}.", ephemeral=True
        )

    @leveling.command(name="voice-interval", description="How often voice XP is given, in seconds (60 or more).")
    @app_commands.rename(seconds_="seconds")
    async def voice_interval(
        self, interaction: discord.Interaction, seconds_: app_commands.Range[int, 60, 3600]
    ) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.update(interaction.guild.id, voice_xp_interval=seconds_)
        await reply(interaction,
            f"✅ Voice now earns {config.voice_xp_amount} XP every {seconds(seconds_)}.\n"
            "-# Voice XP is checked once a minute, so intervals are effectively rounded up to whole minutes.",
            ephemeral=True,
        )

    @leveling.command(name="voice-rules", description="Who earns voice XP. Run with no options to see the rules.")
    @app_commands.describe(
        require_others="Only earn when at least one other person is in the channel",
        min_session="Seconds in voice before earning starts (0–3600)",
        ignore_muted="Muted members don't earn",
        ignore_deafened="Deafened members don't earn",
    )
    async def voice_rules(
        self,
        interaction: discord.Interaction,
        require_others: bool | None = None,
        min_session: app_commands.Range[int, 0, 3600] | None = None,
        ignore_muted: bool | None = None,
        ignore_deafened: bool | None = None,
    ) -> None:
        assert interaction.guild is not None
        changes = {
            name: value
            for name, value in {
                "voice_require_others": require_others,
                "voice_min_session": min_session,
                "voice_ignore_muted": ignore_muted,
                "voice_ignore_deafened": ignore_deafened,
            }.items()
            if value is not None
        }
        if changes:
            config = await self.bot.guild_config.update(interaction.guild.id, **changes)
            heading = "✅ Voice rules updated:"
        else:
            config = await self.bot.guild_config.get(interaction.guild.id)
            heading = "Voice rules:"
        await reply(interaction, f"{heading}\n{voice_rules_text(config)}", ephemeral=True)

    # ------------------------------------------------------------------ level-up messages

    @leveling.command(name="levelup", description="Turn level-up announcements on or off.")
    async def levelup(self, interaction: discord.Interaction, enabled: bool) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, levelup_enabled=enabled)
        await reply(interaction,
            f"✅ Level-up announcements turned {'on' if enabled else 'off'}.", ephemeral=True
        )

    @leveling.command(name="levelup-channel", description="Where level-ups are announced (empty = where the member chatted).")
    @app_commands.describe(channel="Leave empty to announce in the channel where the member leveled up")
    async def levelup_channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        assert interaction.guild is not None
        if channel is not None and (problem := channel_problem_reply(channel)):
            await reply(interaction, f"❌ {problem}", ephemeral=True)
            return
        await self.bot.guild_config.update(interaction.guild.id, levelup_channel_id=channel.id if channel else None)
        if channel:
            message = f"✅ Level-ups will be announced in {channel.mention}."
        else:
            message = (
                "✅ Level-ups will be announced where the member was chatting.\n"
                "-# Voice level-ups are only announced when a level-up channel is set."
            )
        await reply(interaction, message, ephemeral=True)

    @leveling.command(name="levelup-text", description="Edit the level-up announcement text.", extras={DEFER: False})
    async def levelup_text(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)

        async def save(modal_interaction: discord.Interaction, value: str) -> None:
            assert modal_interaction.guild is not None and isinstance(modal_interaction.user, discord.Member)
            await self.bot.guild_config.update(modal_interaction.guild.id, levelup_text=value)
            example = render(value, modal_interaction.user, level=5)
            await reply(modal_interaction,
                f"✅ Level-up text saved. For you reaching level 5 it reads:\n\n{example}"[:MAX_CONTENT],
                ephemeral=True,
            )

        await interaction.response.send_modal(
            TemplateModal(
                title="Level-up text",
                label="Text ({user}, {level}, {server}, …)",
                current=config.levelup_text,
                max_length=MAX_LEVELUP_TEXT,
                on_save=save,
            )
        )

    # ------------------------------------------------------------------ level roles

    @role.command(name="add", description="Give a role at a level. Without a role, a 'Level N' role is created.")
    @app_commands.describe(level="The level that earns the role", role="Existing role to give (leave empty to auto-create)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_add(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 1000],
        role: discord.Role | None = None,
    ) -> None:
        guild = interaction.guild
        assert guild is not None and isinstance(interaction.user, discord.Member)

        if role is not None:
            if problem := role_problem(guild, role):
                await reply(interaction, f"❌ {problem}.", ephemeral=True)
                return
            if interaction.user.id != guild.owner_id and role >= interaction.user.top_role:
                await reply(interaction,
                    f"❌ {role.mention} is at or above your highest role, so you can't hand it out.", ephemeral=True
                )
                return
            existing = next((m for m in await self.bot.roles.milestones(guild.id) if m.role_id == role.id), None)
            if existing is not None and existing.level != level:
                await reply(interaction,
                    f"❌ {role.mention} is already the level {existing.level} role.", ephemeral=True
                )
                return
        elif not guild.me.guild_permissions.manage_roles:
            await reply(interaction,
                "❌ I need the Manage Roles permission to create level roles.", ephemeral=True
            )
            return

        await self.bot.roles.set_milestone(guild.id, level, role.id if role else None)
        given = role.mention if role else f"a new **Level {level}** role (created the first time someone reaches it)"
        await reply(interaction,
            f"✅ Members who reach level {level} get {given}.\n"
            "-# Members already past this level get it the next time they earn XP, "
            "or right away with `/leveling role sync`.",
            ephemeral=True,
        )

    @role.command(name="remove", description="Stop giving a role at a level.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]) -> None:
        assert interaction.guild is not None
        if not await self.bot.roles.remove_milestone(interaction.guild.id, level):
            await reply(interaction, f"❌ There's no level role at level {level}.", ephemeral=True)
            return
        await reply(interaction,
            f"✅ Level {level} no longer gives a role.\n"
            "-# Members who already have the role keep it. Remove or delete it in Server Settings → Roles if you want.",
            ephemeral=True,
        )

    @role.command(name="list", description="Show the level roles.")
    async def role_list(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)
        milestones = await self.bot.roles.milestones(interaction.guild.id)
        await reply(interaction,
            embed=role_list_embed(interaction.guild, milestones, config.level_role_mode), ephemeral=True
        )

    @role.command(name="mode", description="Keep only the highest level role, or all reached ones.")
    @app_commands.choices(mode=ROLE_MODE_CHOICES)
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_mode(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, level_role_mode=mode.value)
        await reply(interaction,
            f"✅ Role mode: {mode.name}.\n-# Applied as members earn XP, or right away with `/leveling role sync`.",
            ephemeral=True,
        )

    @role.command(name="sync", description="Fix everyone's level roles now.")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def role_sync(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        if not await self.bot.roles.milestones(guild.id):
            await reply(interaction,
                "There are no level roles yet. Add one with `/leveling role add`.", ephemeral=True
            )
            return
        await defer(interaction, ephemeral=True, thinking=True)
        await interaction.followup.send(await self._sync_everyone(guild), ephemeral=True)

    async def _sync_everyone(self, guild: discord.Guild) -> str:
        """Sync level roles for everyone who should have, or currently has, one. Returns a summary."""
        milestones = await self.bot.roles.milestones(guild.id)
        if not milestones:
            return "No level roles to sync."
        async with self.bot.community_db.session() as session:
            ranked = await LevelRoleRepo(session).members_at_or_above(guild.id, min(m.level for m in milestones))
        levels = dict(ranked)
        role_ids = {m.role_id for m in milestones if m.role_id}
        holders = {member.id for member in guild.members if any(r.id in role_ids for r in member.roles)}

        changed, problems = 0, set()
        for user_id in [*levels, *(holders - levels.keys())]:
            member = guild.get_member(user_id)
            if member is None or member.bot:
                continue
            result = await self.bot.roles.sync_member(member, levels.get(user_id, 0))
            changed += result.changed
            problems.update(result.problems)

        summary = f"✅ Checked level roles for {len(levels.keys() | holders):,} member(s); updated {changed:,}."
        if problems:
            summary += "\n⚠️ Problems:\n" + "\n".join(f"- {p}" for p in sorted(problems)[:10])
        return summary[:MAX_CONTENT]

    # ------------------------------------------------------------------ XP tools

    async def _after_xp_change(
        self, interaction: discord.Interaction, member: discord.Member, result: AwardResult, action: str
    ) -> None:
        sync = await self.bot.roles.sync_member(member, result.new_level)
        level = f"level {result.new_level}"
        if result.new_level != result.old_level:
            level += f" (was {result.old_level})"
        message = f"✅ {action} {member.mention} now has **{result.total_xp:,} XP**, {level}."
        if sync.added or sync.removed:
            message += f"\n-# Roles: {', '.join([*('+' + r for r in sync.added), *('−' + r for r in sync.removed)])}"
        if sync.problems:
            message += "\n⚠️ " + "; ".join(sync.problems[:3])
        await reply(interaction, message, ephemeral=True)

    async def _refuse_bots(self, interaction: discord.Interaction, member: discord.Member) -> bool:
        if member.bot:
            await reply(interaction, "❌ Bots don't have XP.", ephemeral=True)
            return True
        return False

    @xp.command(name="set", description="Set a member's total XP. Their level and level roles follow.")
    async def xp_set(
        self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 0, MAX_XP]
    ) -> None:
        assert interaction.guild is not None
        if await self._refuse_bots(interaction, member):
            return
        result = await self.bot.leveling.set_xp(interaction.guild.id, member.id, amount, name=member.display_name)
        await self._after_xp_change(interaction, member, result, "Set.")

    @xp.command(name="add", description="Give a member XP (use a negative amount to take XP away).")
    async def xp_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, -MAX_XP, MAX_XP],
    ) -> None:
        assert interaction.guild is not None
        if await self._refuse_bots(interaction, member):
            return
        if amount == 0:
            await reply(interaction, "❌ Give a non-zero amount.", ephemeral=True)
            return
        result = await self.bot.leveling.adjust_xp(interaction.guild.id, member.id, amount, name=member.display_name)
        action = f"Added {amount:,} XP." if amount > 0 else f"Removed {-amount:,} XP."
        await self._after_xp_change(interaction, member, result, action)

    @xp.command(name="reset", description="Delete one member's XP and level.")
    async def xp_reset(self, interaction: discord.Interaction, member: discord.Member) -> None:
        assert interaction.guild is not None
        if await self._refuse_bots(interaction, member):
            return
        previous = await self.bot.leveling.reset_member(interaction.guild.id, member.id)
        if previous is None:
            await reply(interaction, f"{member.mention} has no XP to reset.", ephemeral=True)
            return
        sync = await self.bot.roles.sync_member(member, 0)
        message = f"✅ Reset {member.mention} (had {previous:,} XP).\n-# Undo with `/leveling xp set` and {previous}."
        if sync.removed:
            message += f"\n-# Removed roles: {', '.join(sync.removed)}"
        await reply(interaction, message, ephemeral=True)

    @xp.command(name="reset-all", description="Delete everyone's XP in this server. Asks for confirmation.")
    async def xp_reset_all(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        assert guild is not None
        ranked = (await self.bot.leveling.leaderboard(guild.id, 0, 1))[1]
        if ranked == 0:
            await reply(interaction, "Nobody has XP yet; there's nothing to reset.", ephemeral=True)
            return

        view = ConfirmView(interaction.user.id, confirm_label=f"Reset {ranked:,} members")
        await reply(interaction,
            f"⚠️ This deletes the XP and levels of **{ranked:,} member(s)** in **{guild.name}** and takes away "
            "their level roles. **It can't be undone.**\n-# This expires in 30 seconds.",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if view.confirmed is not True:
            reason = "Cancelled." if view.confirmed is False else "Timed out, nothing was reset."
            await interaction.edit_original_response(content=reason, view=None)
            return

        count = await self.bot.leveling.reset_guild(guild.id)
        log.warning("XP reset for all of guild %s requested by %s", guild.id, interaction.user.id)
        summary = await self._sync_everyone(guild)
        await interaction.followup.send(f"✅ Reset XP for {count:,} member(s).\n{summary}"[:MAX_CONTENT], ephemeral=True)


# ---------------------------------------------------------------------- embeds and text


def voice_rules_text(config: GuildConfig) -> str:
    return "\n".join(
        [
            f"- Needs someone else in the channel: {on_off(config.voice_require_others)}",
            f"- Time in voice before earning: {seconds(config.voice_min_session)}",
            f"- Muted members earn: {on_off(not config.voice_ignore_muted)}",
            f"- Deafened members earn: {on_off(not config.voice_ignore_deafened)}",
            "- Bots and the AFK channel never earn",
        ]
    )


def settings_embed(guild: discord.Guild, config: GuildConfig, milestones: list[Milestone]) -> discord.Embed:
    embed = discord.Embed(title="Leveling settings", colour=discord.Colour.blurple())
    embed.add_field(name="Status", value="✅ Enabled" if config.leveling_enabled else "⏸️ Disabled", inline=False)

    message_amount = (
        f"{config.message_xp_min}" if config.message_xp_min == config.message_xp_max
        else f"{config.message_xp_min}–{config.message_xp_max}"
    )
    embed.add_field(
        name="Message XP",
        value=f"{message_amount} XP per message\nCooldown: {seconds(config.message_xp_cooldown)}",
    )
    embed.add_field(
        name="Voice XP",
        value=f"{config.voice_xp_amount} XP every {seconds(config.voice_xp_interval)}",
    )
    embed.add_field(name="Voice rules", value=voice_rules_text(config), inline=False)

    if config.levelup_channel_id is None:
        destination = "Where the member chatted (voice level-ups not announced)"
    else:
        channel, problem = resolve_channel(guild, config.levelup_channel_id)
        destination = channel.mention if channel else "Not set"
        if problem:
            destination += f"\n⚠️ {problem}"
    embed.add_field(
        name="Level-up messages",
        value=f"{on_off(config.levelup_enabled)} · {destination}\n```\n{config.levelup_text[:500]}\n```",
        inline=False,
    )

    mode = "highest only" if config.level_role_mode == RoleMode.HIGHEST.value else "keep all"
    roles = f"{len(milestones)} milestone(s), {mode}" if milestones else "None"
    embed.add_field(name="Level roles", value=f"{roles}\n-# Details: /leveling role list", inline=False)
    embed.set_footer(text="Level n needs 100 × n² total XP (level 10 = 10,000 XP)")
    return brand(embed, guild)


def role_list_embed(guild: discord.Guild, milestones: list[Milestone], mode: str) -> discord.Embed:
    embed = discord.Embed(title="Level roles", colour=discord.Colour.blurple())
    lines = []
    for milestone in milestones:
        role = guild.get_role(milestone.role_id) if milestone.role_id else None
        if milestone.role_id is None:
            value = f"*Level {milestone.level}* (created the first time someone reaches it)"
        elif role is None:
            value = "⚠️ role was deleted (a new one is created the next time it's needed)"
        else:
            value = role.mention
            if problem := role_problem(guild, role):
                value += f" ⚠️ {problem}"
        lines.append(f"**Level {milestone.level}** → {value}")
    embed.description = "\n".join(lines) if lines else "No level roles yet. Add one with `/leveling role add`."
    mode_text = "highest only" if mode == RoleMode.HIGHEST.value else "keep all reached roles"
    embed.set_footer(text=f"Mode: {mode_text} · Change with /leveling role mode")
    return brand(embed, guild)


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(LevelingAdminCog(bot))
