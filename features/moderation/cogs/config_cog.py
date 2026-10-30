import copy
from typing import Literal
from urllib.parse import urlsplit

import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import embeds
from features.moderation.utils.time import humanize, parse_duration

FILTERS = Literal["all", "badwords", "invites", "links", "spam", "duplicates", "mentions"]


def _channel(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "Not set"


def _on(value: bool) -> str:
    return "🟢 On" if value else "🔴 Off"


class Config(commands.Cog):
    """Per-server settings. Every command here requires Manage Server."""

    config = app_commands.Group(
        name="config",
        description="Configure the moderation bot for this server",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    automod = app_commands.Group(name="automod", description="Automod settings", parent=config)
    badword = app_commands.Group(name="badword", description="Manage the banned word list", parent=config)
    whitelist = app_commands.Group(name="whitelist", description="Manage allowed link domains", parent=config)
    escalation = app_commands.Group(name="escalation", description="Automatic punishments at warn counts",
                                    parent=config)

    def __init__(self, bot):
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.guild_permissions.manage_guild:
            raise app_commands.MissingPermissions(["manage_guild"])
        return True

    async def _automod(self, guild_id: int) -> dict:
        # Copy so a failed save can't leave the cached settings modified.
        return copy.deepcopy((await self.bot.mod_configs.get(guild_id)).automod)

    async def _save_automod(self, guild_id: int, automod: dict) -> None:
        await self.bot.mod_configs.update(guild_id, automod=automod)

    # ---- overview -----------------------------------------------------

    @config.command(name="view", description="Show the current configuration")
    async def view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        s = await self.bot.mod_configs.get(interaction.guild_id)
        am, raid = s.automod, s.raid
        thresholds = await self.bot.mod_db.list_thresholds(interaction.guild_id)

        embed = embeds.info(f"Configuration for {interaction.guild.name}", guild=interaction.guild)
        embed.add_field(name="Channels", value=(
            f"Mod-log: {_channel(s.modlog_channel_id)}\n"
            f"Reports: {_channel(s.report_channel_id)}\n"
            f"Alerts: {_channel(s.alert_channel_id)}"
        ), inline=False)
        embed.add_field(name="Punishment DMs", value=(
            f"{_on(s.dm_on_action)}\nAppeal link: {s.appeal_url or 'Not set'}"
        ), inline=False)
        embed.add_field(name="Automod", value=(
            f"Master: {_on(am['enabled'])}\n"
            f"Bad words: {_on(am['badwords'])} ({len(s.banned_words)} words)\n"
            f"Invites: {_on(am['invites'])}\n"
            f"Links: {_on(am['links'])} ({len(s.whitelist)} whitelisted)\n"
            f"Spam: {_on(am['spam']['enabled'])} ({am['spam']['messages']} msgs / {am['spam']['seconds']}s)\n"
            f"Duplicates: {_on(am['duplicates']['enabled'])} "
            f"({am['duplicates']['count']} repeats / {am['duplicates']['seconds']}s)\n"
            f"Mentions: {_on(am['mentions']['enabled'])} (max {am['mentions']['max']})\n"
            f"Exempt roles: {' '.join(f'<@&{r}>' for r in am['exempt_roles']) or 'None'}\n"
            f"Exempt channels: {' '.join(f'<#{c}>' for c in am['exempt_channels']) or 'None'}"
        ), inline=False)
        embed.add_field(name="Anti-raid", value=(
            f"{_on(raid['enabled'])} ({raid['joins']} joins / {raid['seconds']}s)\n"
            f"Min account age: {raid['min_account_age_days']} day(s)\n"
            f"Raid mode: {'🚨 ACTIVE' if s.raidmode_active else 'Inactive'}"
        ), inline=False)
        embed.add_field(name="Warn escalation", value="\n".join(
            f"{t['warn_count']} warns → {t['action']}"
            + (f" ({humanize(t['duration_seconds'])})" if t["duration_seconds"] else "")
            for t in thresholds
        ) or "None configured", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---- channels & DMs -----------------------------------------------

    @config.command(name="modlog", description="Set the channel where moderation actions are logged")
    @app_commands.describe(channel="Leave empty to disable mod-logging")
    async def modlog(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await self.bot.mod_configs.update(interaction.guild_id, modlog_channel_id=channel and channel.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Mod-log channel set to {_channel(channel and channel.id)}."), ephemeral=True)

    @config.command(name="reports", description="Set the channel where user reports are sent")
    @app_commands.describe(channel="Leave empty to disable reports")
    async def reports(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await self.bot.mod_configs.update(interaction.guild_id, report_channel_id=channel and channel.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Report channel set to {_channel(channel and channel.id)}."), ephemeral=True)

    @config.command(name="alerts", description="Set the channel for raid alerts (defaults to the mod-log)")
    async def alerts(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        await self.bot.mod_configs.update(interaction.guild_id, alert_channel_id=channel and channel.id)
        await interaction.response.send_message(
            embed=embeds.success(f"Alert channel set to {_channel(channel and channel.id)}."), ephemeral=True)

    @config.command(name="appeal", description="Set the appeal link included in punishment DMs")
    @app_commands.describe(url="An https:// link; leave empty to remove")
    async def appeal(self, interaction: discord.Interaction, url: str | None = None):
        if url and urlsplit(url).scheme not in {"http", "https"}:
            await interaction.response.send_message(embed=embeds.error("That isn't a valid http(s) link."),
                                                    ephemeral=True)
            return
        await self.bot.mod_configs.update(interaction.guild_id, appeal_url=url)
        await interaction.response.send_message(
            embed=embeds.success(f"Appeal link {'set to ' + url if url else 'removed'}."), ephemeral=True)

    @config.command(name="dm", description="Turn punishment DMs on or off")
    async def dm(self, interaction: discord.Interaction, enabled: bool):
        await self.bot.mod_configs.update(interaction.guild_id, dm_on_action=enabled)
        await interaction.response.send_message(
            embed=embeds.success(f"Punishment DMs {_on(enabled)}."), ephemeral=True)

    # ---- automod ------------------------------------------------------

    @automod.command(name="toggle", description="Turn an automod filter on or off")
    @app_commands.describe(filter="'all' is the master switch")
    async def automod_toggle(self, interaction: discord.Interaction, filter: FILTERS, enabled: bool):
        am = await self._automod(interaction.guild_id)
        key = "enabled" if filter == "all" else filter
        if isinstance(am[key], dict):
            am[key]["enabled"] = enabled
        else:
            am[key] = enabled
        await self._save_automod(interaction.guild_id, am)
        await interaction.response.send_message(
            embed=embeds.success(f"Automod `{filter}` {_on(enabled)}."), ephemeral=True)

    @automod.command(name="spam", description="Set the message rate limit")
    @app_commands.describe(messages="Messages allowed...", seconds="...within this many seconds")
    async def automod_spam(self, interaction: discord.Interaction,
                           messages: app_commands.Range[int, 2, 30], seconds: app_commands.Range[int, 1, 60]):
        am = await self._automod(interaction.guild_id)
        am["spam"].update(messages=messages, seconds=seconds)
        await self._save_automod(interaction.guild_id, am)
        await interaction.response.send_message(
            embed=embeds.success(f"Spam limit: more than {messages} messages in {seconds}s is spam."),
            ephemeral=True)

    @automod.command(name="duplicates", description="Set the repeated-message limit")
    @app_commands.describe(count="Identical messages allowed...", seconds="...within this many seconds")
    async def automod_duplicates(self, interaction: discord.Interaction,
                                 count: app_commands.Range[int, 2, 20], seconds: app_commands.Range[int, 5, 600]):
        am = await self._automod(interaction.guild_id)
        am["duplicates"].update(count=count, seconds=seconds)
        await self._save_automod(interaction.guild_id, am)
        await interaction.response.send_message(
            embed=embeds.success(f"Duplicate limit: {count} identical messages in {seconds}s."), ephemeral=True)

    @automod.command(name="mentions", description="Set the maximum mentions allowed in one message")
    async def automod_mentions(self, interaction: discord.Interaction, max: app_commands.Range[int, 1, 50]):
        am = await self._automod(interaction.guild_id)
        am["mentions"]["max"] = max
        await self._save_automod(interaction.guild_id, am)
        await interaction.response.send_message(
            embed=embeds.success(f"Messages with more than {max} mentions will be removed."), ephemeral=True)

    @automod.command(name="exempt", description="Toggle an automod exemption for a role or channel")
    async def automod_exempt(self, interaction: discord.Interaction,
                             role: discord.Role | None = None, channel: discord.TextChannel | None = None):
        if role is None and channel is None:
            await interaction.response.send_message(embed=embeds.error("Pick a role or a channel."), ephemeral=True)
            return
        am = await self._automod(interaction.guild_id)
        changes = []
        for key, target in (("exempt_roles", role), ("exempt_channels", channel)):
            if target is None:
                continue
            if target.id in am[key]:
                am[key].remove(target.id)
                changes.append(f"{target.mention} is no longer exempt")
            else:
                am[key].append(target.id)
                changes.append(f"{target.mention} is now exempt")
        await self._save_automod(interaction.guild_id, am)
        await interaction.response.send_message(embed=embeds.success("\n".join(changes)), ephemeral=True)

    # ---- banned words -------------------------------------------------

    @badword.command(name="add", description="Add a banned word or phrase")
    async def badword_add(self, interaction: discord.Interaction, word: app_commands.Range[str, 1, 100]):
        word = word.strip().lower()
        added = await self.bot.mod_db.add_banned_word(interaction.guild_id, word)
        self.bot.mod_configs.invalidate(interaction.guild_id)
        message = f"Added ||{word}|| to the banned list." if added else "That word is already banned."
        await interaction.response.send_message(embed=embeds.success(message), ephemeral=True)

    @badword.command(name="remove", description="Remove a banned word or phrase")
    async def badword_remove(self, interaction: discord.Interaction, word: str):
        removed = await self.bot.mod_db.remove_banned_word(interaction.guild_id, word.strip().lower())
        self.bot.mod_configs.invalidate(interaction.guild_id)
        embed = embeds.success("Word removed.") if removed else embeds.error("That word isn't on the list.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @badword.command(name="list", description="Show the banned word list")
    async def badword_list(self, interaction: discord.Interaction):
        words = await self.bot.mod_db.get_banned_words(interaction.guild_id)
        text = ", ".join(f"||{w}||" for w in words) or "No banned words."
        await interaction.response.send_message(embed=embeds.info("Banned words", embeds.truncate(text, 4000), interaction.guild),
                                                ephemeral=True)

    # ---- link whitelist -----------------------------------------------

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        domain = domain.strip().lower()
        if "://" in domain:
            domain = urlsplit(domain).hostname or ""
        return domain.removeprefix("www.").strip("/")

    @whitelist.command(name="add", description="Allow links to a domain (subdomains included)")
    @app_commands.describe(domain="e.g. youtube.com")
    async def whitelist_add(self, interaction: discord.Interaction, domain: str):
        domain = self._normalize_domain(domain)
        if not domain or "." not in domain:
            await interaction.response.send_message(embed=embeds.error("That isn't a valid domain."), ephemeral=True)
            return
        await self.bot.mod_db.add_whitelist(interaction.guild_id, domain)
        self.bot.mod_configs.invalidate(interaction.guild_id)
        await interaction.response.send_message(embed=embeds.success(f"`{domain}` is now allowed."), ephemeral=True)

    @whitelist.command(name="remove", description="Remove a domain from the whitelist")
    async def whitelist_remove(self, interaction: discord.Interaction, domain: str):
        removed = await self.bot.mod_db.remove_whitelist(interaction.guild_id, self._normalize_domain(domain))
        self.bot.mod_configs.invalidate(interaction.guild_id)
        embed = embeds.success("Domain removed.") if removed else embeds.error("That domain isn't whitelisted.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @whitelist.command(name="list", description="Show whitelisted domains")
    async def whitelist_list(self, interaction: discord.Interaction):
        domains = await self.bot.mod_db.get_whitelist(interaction.guild_id)
        text = "\n".join(f"`{d}`" for d in domains) or "No whitelisted domains."
        await interaction.response.send_message(embed=embeds.info("Link whitelist", embeds.truncate(text, 4000), interaction.guild),
                                                ephemeral=True)

    # ---- warn escalation ----------------------------------------------

    @escalation.command(name="set", description="Punish automatically when a member reaches a warn count")
    @app_commands.describe(warns="Number of active warnings", action="Punishment to apply",
                           duration="For timeout (required) or ban (optional = tempban), e.g. 1h, 7d")
    async def escalation_set(self, interaction: discord.Interaction, warns: app_commands.Range[int, 1, 50],
                             action: Literal["timeout", "kick", "ban"], duration: str | None = None):
        seconds = None
        if duration:
            delta = parse_duration(duration)
            if delta is None:
                await interaction.response.send_message(
                    embed=embeds.error("Invalid duration. Use formats like `30m`, `2h`, `7d`."), ephemeral=True)
                return
            seconds = int(delta.total_seconds())
        if action == "timeout" and not seconds:
            await interaction.response.send_message(embed=embeds.error("A timeout needs a duration."), ephemeral=True)
            return
        if action == "timeout" and seconds > 28 * 86400:
            await interaction.response.send_message(embed=embeds.error("Timeouts can be at most 28 days."),
                                                    ephemeral=True)
            return
        if action == "kick":
            seconds = None
        await self.bot.mod_db.set_threshold(interaction.guild_id, warns, action, seconds)
        extra = f" for {humanize(seconds)}" if seconds else ""
        await interaction.response.send_message(
            embed=embeds.success(f"At **{warns}** warnings: **{action}**{extra}."), ephemeral=True)

    @escalation.command(name="remove", description="Remove an escalation rule")
    async def escalation_remove(self, interaction: discord.Interaction, warns: int):
        removed = await self.bot.mod_db.delete_threshold(interaction.guild_id, warns)
        embed = embeds.success("Rule removed.") if removed else embeds.error(f"No rule for {warns} warnings.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---- anti-raid ----------------------------------------------------

    @config.command(name="antiraid", description="Configure join-flood detection")
    @app_commands.describe(enabled="Detect join floods", joins="Joins that trigger raid mode...",
                           seconds="...within this many seconds",
                           min_account_age_days="Kick accounts younger than this (0 = off)")
    async def antiraid(self, interaction: discord.Interaction, enabled: bool | None = None,
                       joins: app_commands.Range[int, 2, 100] | None = None,
                       seconds: app_commands.Range[int, 1, 300] | None = None,
                       min_account_age_days: app_commands.Range[int, 0, 365] | None = None):
        raid = copy.deepcopy((await self.bot.mod_configs.get(interaction.guild_id)).raid)
        for key, value in (("enabled", enabled), ("joins", joins), ("seconds", seconds),
                           ("min_account_age_days", min_account_age_days)):
            if value is not None:
                raid[key] = value
        await self.bot.mod_configs.update(interaction.guild_id, raid=raid)
        await interaction.response.send_message(embed=embeds.success(
            f"Anti-raid {_on(raid['enabled'])}: {raid['joins']} joins in {raid['seconds']}s triggers raid mode. "
            f"Min account age: {raid['min_account_age_days']} day(s)."
        ), ephemeral=True)


async def setup(bot):
    await bot.add_cog(Config(bot))
