"""Listens to Discord events and turns them into log embeds for the 14 logging channels."""

from __future__ import annotations

import asyncio
import datetime
import enum
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

import discord
from discord.ext import commands

from features.logging import settings as config
from features.logging.services.audit_handler import audit_actions, changed_keys, safe_changes, safe_target
from features.logging.services.log_router import UNAVAILABLE, LogFile, add_field, format_duration, make_embed, truncate

if TYPE_CHECKING:
    from core.bot import Bot as LoggerBot

log = logging.getLogger(__name__)

A = discord.AuditLogAction
_MISSING = object()

# Audit actions already logged (with attribution) by a dedicated gateway handler below.
HANDLED_BY_GATEWAY = audit_actions(
    "ban", "unban", "member_update", "member_role_update", "bot_add",
    "role_create", "role_update", "role_delete",
    "channel_create", "channel_update", "channel_delete",
    "overwrite_create", "overwrite_update", "overwrite_delete",
    "guild_update", "invite_create", "invite_update", "invite_delete",
    "message_delete", "message_bulk_delete",
)  # fmt: skip

# Audit actions with no useful gateway event, routed straight from the audit entry.
AUDIT_ROUTES: dict[str, frozenset[discord.AuditLogAction]] = {
    "moderation": audit_actions(
        "kick", "member_prune",
        "automod_rule_create", "automod_rule_update", "automod_rule_delete",
        "automod_block_message", "automod_flag_message", "automod_timeout_member", "automod_quarantine_user",
    ),
    "voice": audit_actions("member_move", "member_disconnect"),
    "bot": audit_actions(
        "webhook_create", "webhook_update", "webhook_delete",
        "integration_create", "integration_update", "integration_delete",
        "app_command_permission_update",
    ),
}  # fmt: skip

# Discord audit action types that discord.py doesn't name yet.
UNKNOWN_ACTION_NAMES = {
    192: "voice_channel_status_set",
    193: "voice_channel_status_cleared",
}

CHANNEL_ATTRS = {
    "name": "Name",
    "category": "Category",
    "topic": "Topic",
    "nsfw": "Age-restricted",
    "slowmode_delay": "Slowmode (s)",
    "bitrate": "Bitrate",
    "user_limit": "User limit",
    "rtc_region": "Region",
    "video_quality_mode": "Video quality",
    "default_auto_archive_duration": "Thread auto-archive (min)",
}
ROLE_ATTRS = {
    "name": "Name",
    "colour": "Colour",
    "hoist": "Shown separately",
    "mentionable": "Mentionable",
    "icon": "Icon",
    "unicode_emoji": "Emoji",
}
GUILD_ATTRS = {
    "name": "Name",
    "icon": "Icon",
    "banner": "Banner",
    "splash": "Invite splash",
    "description": "Description",
    "owner": "Owner",
    "verification_level": "Verification level",
    "explicit_content_filter": "Content filter",
    "default_notifications": "Default notifications",
    "mfa_level": "2FA requirement",
    "afk_channel": "AFK channel",
    "afk_timeout": "AFK timeout (s)",
    "system_channel": "System channel",
    "rules_channel": "Rules channel",
    "public_updates_channel": "Community updates channel",
    "vanity_url_code": "Vanity URL",
    "preferred_locale": "Primary language",
    "premium_progress_bar_enabled": "Boost progress bar",
}
VOICE_FLAGS = {
    "self_mute": "Self mute",
    "self_deaf": "Self deafen",
    "mute": "Server mute",
    "deaf": "Server deafen",
    "self_stream": "Streaming",
    "self_video": "Camera",
    "suppress": "Stage suppressed",
}


@dataclass
class VoiceSession:
    """In-memory voice timer for one member (resets if the bot restarts)."""

    channel_id: int
    channel_since: datetime.datetime  # when they entered the current channel
    session_since: datetime.datetime  # when they first connected to voice
    approximate: bool = False  # channel timer started at bot startup, not at the real join
    session_approximate: bool | None = None

    def __post_init__(self) -> None:
        if self.session_approximate is None:
            self.session_approximate = self.approximate


# --- formatting helpers ----------------------------------------------------------


def user_text(user: discord.abc.User | None) -> str:
    if user is None:
        return UNAVAILABLE
    return f"{user.mention} (`{user}` · `{user.id}`)"


def channel_text(channel: Any) -> str:
    if channel is None:
        return UNAVAILABLE
    name = getattr(channel, "name", None)
    mention = getattr(channel, "mention", f"`{channel.id}`")
    return f"{mention} (`{name}`)" if name else mention


def time_text(dt: datetime.datetime | None) -> str:
    if dt is None:
        return UNAVAILABLE
    return f"{discord.utils.format_dt(dt, 'f')} ({discord.utils.format_dt(dt, 'R')})"


_NAME_ALIASES = {"read_messages": "view_channel", "manage_guild": "manage_server"}


def pretty(name: str) -> str:
    return _NAME_ALIASES.get(name, name).replace("_", " ").capitalize()


def fmt(value: Any) -> str:
    """Render an arbitrary attribute value for a before → after line."""
    if value is None:
        return "*None*"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, discord.Asset):
        return f"[image]({value.url})"
    if isinstance(value, (discord.Role, discord.abc.GuildChannel, discord.Thread, discord.abc.User)):
        return value.mention
    if isinstance(value, discord.Object):
        return f"`{value.id}`"
    if isinstance(value, enum.Enum) or type(value).__module__ == "discord.enums":
        # discord.py enums are tuple-like values, not enum.Enum; unknown ones are named "unknown_<value>".
        if value.name.startswith("unknown_"):
            return fmt(value.value)
        return pretty(value.name)
    if isinstance(value, datetime.datetime):
        return discord.utils.format_dt(value, "f")
    if isinstance(value, str):
        return f"`{truncate(value, 200)}`" if value else "*empty*"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(fmt(v) for v in value) or "*none*"
    return truncate(str(value), 200)


def diff_attrs(before: Any, after: Any, attrs: dict[str, str]) -> list[str]:
    lines = []
    for attr, label in attrs.items():
        old, new = getattr(before, attr, _MISSING), getattr(after, attr, _MISSING)
        if old is _MISSING or new is _MISSING or old == new:
            continue
        lines.append(f"**{label}:** {fmt(old)} → {fmt(new)}")
    return lines


def permission_changes(before: discord.Permissions, after: discord.Permissions) -> tuple[list[str], list[str]]:
    granted = [pretty(n) for n, v in after if v and not getattr(before, n)]
    revoked = [pretty(n) for n, v in after if not v and getattr(before, n)]
    return granted, revoked


def overwrite_changes(
    before: dict[Any, discord.PermissionOverwrite], after: dict[Any, discord.PermissionOverwrite]
) -> list[str]:
    symbols = {True: "✅", False: "❌", None: "⬜"}
    # Key by ID: the same member can appear as a Member in one snapshot and an Object in the other.
    before_by_id = {t.id: (t, ow) for t, ow in before.items()}
    after_by_id = {t.id: (t, ow) for t, ow in after.items()}
    lines = []
    for target_id in {*before_by_id, *after_by_id}:
        target = (after_by_id.get(target_id) or before_by_id[target_id])[0]
        label = getattr(target, "mention", f"`{target_id}`")
        old = before_by_id[target_id][1] if target_id in before_by_id else None
        new = after_by_id[target_id][1] if target_id in after_by_id else None
        if old is None and new is not None:
            lines.append(f"➕ Override added for {label}")
            old = discord.PermissionOverwrite()
        elif new is None:
            lines.append(f"➖ Override removed for {label}")
            continue
        elif old == new:
            continue
        else:
            lines.append(f"✏️ Override changed for {label}")
        for name, value in new:
            if getattr(old, name) != value:
                lines.append(f"  {symbols[getattr(old, name)]} → {symbols[value]} {pretty(name)}")
    return lines


def join_lines(lines: Iterable[str], limit: int = 1024) -> str:
    text = "\n".join(lines)
    return truncate(text, limit) if text else "*None*"


class LoggingEvents(commands.Cog):
    def __init__(self, bot: LoggerBot) -> None:
        self.bot = bot
        self.router = bot.log_router
        self.audit = bot.audit
        self._invites: dict[int, dict[str, int]] = {}
        self._invite_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._voice_sessions: dict[tuple[int, int], VoiceSession] = {}

    def enabled(self, guild: discord.Guild | None) -> bool:
        return self.router.is_enabled(guild)

    def send(self, guild: discord.Guild, key: str, embed: discord.Embed, files: list[LogFile] | None = None) -> None:
        self.router.send(guild, key, embed, files)

    def actor_field(
        self, embed: discord.Embed, guild: discord.Guild, entry: discord.AuditLogEntry | None, name: str = "By", **kwargs: Any
    ) -> None:
        add_field(embed, name, self.audit.actor_text(guild, entry, **kwargs), inline=True)
        if entry is not None and entry.reason:
            add_field(embed, "Reason", entry.reason)

    def target_text(self, guild: discord.Guild, target: Any) -> str:
        if target is None:
            return UNAVAILABLE
        if isinstance(target, discord.abc.User):
            return user_text(target)
        if isinstance(target, (discord.Role, discord.abc.GuildChannel, discord.Thread)):
            return channel_text(target) if not isinstance(target, discord.Role) else f"{target.mention} (`{target.name}`)"
        if isinstance(target, discord.Guild):
            return target.name
        if isinstance(target, discord.Invite):
            return f"`{target.code}`"
        if isinstance(target, discord.Object):
            resolved = guild.get_member(target.id) or guild.get_channel(target.id) or guild.get_role(target.id)
            if resolved is not None:
                return self.target_text(guild, resolved)
            user = self.bot.get_user(target.id)
            return user_text(user) if user else f"ID `{target.id}`"
        name = getattr(target, "name", None)
        return f"`{name}`" if name else truncate(str(target), 200)

    # --- startup ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self._sync_voice_sessions()
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._cache_invites(guild)

    # --- audit log entries -----------------------------------------------------

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        self.audit.record(entry)
        guild = entry.guild
        if entry.action in HANDLED_BY_GATEWAY or not self.enabled(guild):
            return
        key = next((k for k, actions in AUDIT_ROUTES.items() if entry.action in actions), "audit")

        target = safe_target(entry)
        if entry.action is A.kick:
            embed = make_embed("moderation", "👢 Member Kicked")
            add_field(embed, "Member", self.target_text(guild, target), inline=True)
            add_field(embed, "Moderator", self.audit.actor_text(guild, entry), inline=True)
            add_field(embed, "Reason", self.audit.reason_text(entry))
            self.send(guild, "moderation", embed)
            return

        action_name = entry.action.name
        if action_name.startswith("unknown_"):
            # Action types newer than discord.py; name the ones we know about.
            action_value = int(action_name.removeprefix("unknown_"))
            action_name = UNKNOWN_ACTION_NAMES.get(action_value, f"Audit action {action_value}")
            if action_value in UNKNOWN_ACTION_NAMES:
                key = "voice"
            raw_target_id = getattr(entry, "_target_id", None)
            if target is None and raw_target_id:
                target = guild.get_channel(raw_target_id) or discord.Object(id=raw_target_id)
        embed = make_embed(key, f"📋 {pretty(action_name)}")
        add_field(embed, "Performed by", self.audit.actor_text(guild, entry), inline=True)
        if target is not None:
            add_field(embed, "Target", self.target_text(guild, target), inline=True)
        if entry.reason:
            add_field(embed, "Reason", entry.reason)

        changes = safe_changes(entry)
        if changes is None:
            add_field(embed, "Changes", "*Details unavailable: this entry couldn't be decoded.*")
        elif changes:
            lines = [f"**{pretty(attr)}:** {fmt(old)} → {fmt(new)}" for attr, old, new in sorted(changes, key=lambda c: c[0])]
            add_field(embed, "Changes", join_lines(lines[:15]))

        extra = entry.extra
        if extra is not None:
            if type(extra).__name__.startswith("_AuditLogProxy"):
                details = [f"**{pretty(k)}:** {fmt(v)}" for k, v in vars(extra).items() if not k.startswith("_")]
                if details:
                    add_field(embed, "Details", join_lines(details))
            else:
                add_field(embed, "Details", self.target_text(guild, extra))
        embed.set_footer(text=f"Audit entry {entry.id}")
        self.send(guild, key, embed)

    # --- members: join / leave -------------------------------------------------

    async def _cache_invites(self, guild: discord.Guild) -> None:
        if not guild.me.guild_permissions.manage_guild:
            return
        try:
            invites = await guild.invites()
        except discord.HTTPException:
            return
        self._invites[guild.id] = {i.code: i.uses or 0 for i in invites}

    async def _detect_invite(self, guild: discord.Guild) -> tuple[str, discord.Invite | None]:
        if not guild.me.guild_permissions.manage_guild:
            return "Unavailable *(bot lacks Manage Server)*", None
        async with self._invite_locks[guild.id]:
            previous = self._invites.get(guild.id)
            try:
                invites = await guild.invites()
            except discord.HTTPException:
                return "Unavailable *(couldn't fetch invites)*", None
            self._invites[guild.id] = {i.code: i.uses or 0 for i in invites}
        if previous is None:
            return "Unknown *(invite list was still loading)*", None
        used = [i for i in invites if (i.uses or 0) > previous.get(i.code, 0)]
        if len(used) == 1:
            invite = used[0]
            inviter = f" by {invite.inviter.mention}" if invite.inviter else ""
            return f"`{invite.code}`{inviter}", invite
        if len(used) > 1:
            return "Ambiguous *(several invites were used at the same time)*", None
        if guild.vanity_url_code:
            return f"Unknown *(possibly the vanity URL `{guild.vanity_url_code}`, Discovery, or a single-use invite)*", None
        return "Unknown *(possibly Discovery or a single-use invite)*", None

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild = member.guild
        if not self.enabled(guild):
            return

        if member.bot:
            entry = await self.audit.find(guild, A.bot_add, member.id)
            embed = make_embed("bot", "🤖 Bot Added")
            add_field(embed, "Bot", user_text(member), inline=True)
            self.actor_field(embed, guild, entry, "Added by")
            add_field(embed, "Verified bot", "Yes" if member.public_flags.verified_bot else "No", inline=True)
            embed.set_thumbnail(url=member.display_avatar.url)
            self.send(guild, "bot", embed)
            return

        invite_text, invite = await self._detect_invite(guild)
        embed = make_embed("join_leave", "📥 Member Joined", colour=discord.Colour.green())
        add_field(embed, "Member", user_text(member))
        add_field(embed, "Account created", time_text(member.created_at))
        add_field(embed, "Member count", f"{guild.member_count:,}" if guild.member_count else None, inline=True)
        add_field(embed, "Invite", invite_text, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        self.send(guild, "join_leave", embed)

        if invite is not None:
            inv = make_embed("invite", "🔗 Invite Used")
            add_field(inv, "Member", user_text(member))
            add_field(inv, "Invite", f"`{invite.code}`", inline=True)
            add_field(inv, "Created by", user_text(invite.inviter) if invite.inviter else None, inline=True)
            add_field(inv, "Channel", channel_text(invite.channel), inline=True)
            uses = f"{invite.uses or 0}" + (f" / {invite.max_uses}" if invite.max_uses else "")
            add_field(inv, "Uses", uses, inline=True)
            self.send(guild, "invite", inv)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild = member.guild
        if not self.enabled(guild):
            return

        if member.bot:
            entry = await self.audit.find(guild, audit_actions("kick", "ban"), member.id)
            embed = make_embed("bot", "🤖 Bot Removed")
            add_field(embed, "Bot", user_text(member), inline=True)
            self.actor_field(embed, guild, entry, "Removed by", missing="Unknown *(the bot may have left on its own)*")
            self.send(guild, "bot", embed)
            return

        embed = make_embed("join_leave", "📤 Member Left", colour=discord.Colour.dark_orange())
        add_field(embed, "Member", user_text(member))
        add_field(embed, "Joined", time_text(member.joined_at))
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        add_field(embed, f"Roles ({len(roles)})", join_lines([" ".join(roles)]) if roles else "*None*")
        add_field(embed, "Member count", f"{guild.member_count:,}" if guild.member_count else None, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text="Kicks and bans are also recorded in moderation-logs")
        self.send(guild, "join_leave", embed)

    # --- members: updates ------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        if not self.enabled(guild):
            return
        tasks = []

        if before.nick != after.nick:
            tasks.append(self._log_nickname(before, after))
        if before.roles != after.roles:
            tasks.append(self._log_member_roles(before, after))
        if before.timed_out_until != after.timed_out_until:
            tasks.append(self._log_timeout(before, after))

        if before.guild_avatar != after.guild_avatar:
            embed = make_embed("profile", "🖼️ Server Avatar Changed")
            add_field(embed, "Member", user_text(after))
            add_field(embed, "Old avatar", f"[link]({before.guild_avatar.url})" if before.guild_avatar else "*None*", inline=True)
            add_field(embed, "New avatar", f"[link]({after.guild_avatar.url})" if after.guild_avatar else "*None*", inline=True)
            embed.set_thumbnail(url=after.display_avatar.url)
            self.send(guild, "profile", embed)

        if before.premium_since != after.premium_since:
            boosted = after.premium_since is not None
            embed = make_embed("server", "🚀 Member Boosted the Server" if boosted else "🚀 Member Stopped Boosting")
            add_field(embed, "Member", user_text(after))
            add_field(embed, "Total boosts", guild.premium_subscription_count, inline=True)
            self.send(guild, "server", embed)

        if before.pending and not after.pending:
            embed = make_embed("member", "✅ Membership Screening Completed")
            add_field(embed, "Member", user_text(after))
            self.send(guild, "member", embed)

        if before.flags != after.flags:
            gained = [pretty(n) for n, v in after.flags if v and not getattr(before.flags, n)]
            lost = [pretty(n) for n, v in after.flags if not v and getattr(before.flags, n)]
            embed = make_embed("member", "👤 Member Flags Updated")
            add_field(embed, "Member", user_text(after))
            if gained:
                add_field(embed, "Added", ", ".join(gained))
            if lost:
                add_field(embed, "Removed", ", ".join(lost))
            self.send(guild, "member", embed)

        if tasks:
            await asyncio.gather(*tasks)

    async def _log_nickname(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        entry = await self.audit.find(guild, A.member_update, after.id, predicate=lambda e: "nick" in changed_keys(e))
        embed = make_embed("nickname", "🏷️ Nickname Changed")
        add_field(embed, "Member", user_text(after))
        add_field(embed, "Before", fmt(before.nick), inline=True)
        add_field(embed, "After", fmt(after.nick), inline=True)
        if entry is not None and entry.user_id == after.id:
            add_field(embed, "Changed by", "The member themselves", inline=True)
        else:
            self.actor_field(embed, guild, entry, "Changed by")
        self.send(guild, "nickname", embed)

    async def _log_member_roles(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if not added and not removed:
            return
        entry = await self.audit.find(guild, A.member_role_update, after.id)
        embed = make_embed("role", "🔐 Member Roles Updated")
        add_field(embed, "Member", user_text(after))
        if added:
            add_field(embed, "➕ Added", " ".join(r.mention for r in added))
        if removed:
            add_field(embed, "➖ Removed", " ".join(r.mention for r in removed))
        self.actor_field(embed, guild, entry, "Updated by")
        self.send(guild, "role", embed)

    async def _log_timeout(self, before: discord.Member, after: discord.Member) -> None:
        guild = after.guild
        entry = await self.audit.find(
            guild, A.member_update, after.id, predicate=lambda e: "timed_out_until" in changed_keys(e)
        )
        if after.timed_out_until is not None:
            embed = make_embed("moderation", "⏳ Member Timed Out")
            add_field(embed, "Member", user_text(after))
            add_field(embed, "Until", time_text(after.timed_out_until))
        else:
            embed = make_embed("moderation", "✅ Timeout Removed", colour=discord.Colour.green())
            add_field(embed, "Member", user_text(after))
        self.actor_field(embed, guild, entry, "Moderator")
        self.send(guild, "moderation", embed)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        lines = diff_attrs(before, after, {"name": "Username", "global_name": "Display name"})
        avatar_changed = before.avatar != after.avatar
        if not lines and not avatar_changed:
            return
        for guild in self.bot.guilds:
            if guild.get_member(after.id) is None or not self.enabled(guild):
                continue
            embed = make_embed("profile", "🖼️ Profile Updated")
            add_field(embed, "User", user_text(after))
            if lines:
                add_field(embed, "Changes", join_lines(lines))
            if avatar_changed:
                add_field(embed, "Old avatar", f"[link]({before.display_avatar.url})", inline=True)
                add_field(embed, "New avatar", f"[link]({after.display_avatar.url})", inline=True)
            embed.set_thumbnail(url=after.display_avatar.url)
            self.send(guild, "profile", embed)

    # --- moderation ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member) -> None:
        if not self.enabled(guild):
            return
        entry = await self.audit.find(guild, A.ban, user.id)
        embed = make_embed("moderation", "🔨 Member Banned")
        add_field(embed, "User", user_text(user))
        add_field(embed, "Moderator", self.audit.actor_text(guild, entry), inline=True)
        add_field(embed, "Reason", self.audit.reason_text(entry) or UNAVAILABLE)
        embed.set_thumbnail(url=user.display_avatar.url)
        self.send(guild, "moderation", embed)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        if not self.enabled(guild):
            return
        entry = await self.audit.find(guild, A.unban, user.id)
        embed = make_embed("moderation", "🕊️ Member Unbanned", colour=discord.Colour.green())
        add_field(embed, "User", user_text(user))
        self.actor_field(embed, guild, entry, "Moderator")
        self.send(guild, "moderation", embed)

    # --- messages --------------------------------------------------------------

    def _should_log_message(self, message: discord.Message) -> bool:
        if message.guild is None or message.author.bot:
            return False
        return self.enabled(message.guild)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not self._should_log_message(after):
            return
        removed = [a for a in before.attachments if a not in after.attachments]
        if before.content == after.content and not removed:
            return  # e.g. an embed preview loading
        embed = make_embed("message", "✏️ Message Edited")
        add_field(embed, "Author", user_text(after.author), inline=True)
        add_field(embed, "Channel", channel_text(after.channel), inline=True)
        if before.content != after.content:
            add_field(embed, "Before", before.content or "*(no text)*")
            add_field(embed, "After", after.content or "*(no text)*")
        if removed:
            add_field(embed, "📎 Attachments removed", join_lines(f"`{a.filename}`" for a in removed))
        add_field(embed, "Message", f"[Jump to message]({after.jump_url})", inline=True)
        self.send(after.guild, "message", embed)  # type: ignore[arg-type]

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        # Cached messages are handled by on_message_edit with full before/after content.
        if payload.cached_message is not None or payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not self.enabled(guild) or "content" not in payload.data:
            return
        author_data = payload.data.get("author") or {}
        if author_data.get("bot"):
            return
        assert guild is not None
        channel = guild.get_channel_or_thread(payload.channel_id)
        author = guild.get_member(int(author_data["id"])) if author_data.get("id") else None
        embed = make_embed("message", "✏️ Message Edited")
        add_field(embed, "Author", user_text(author) if author else f"`{author_data.get('username', 'Unknown')}`", inline=True)
        add_field(embed, "Channel", channel_text(channel) if channel else f"`{payload.channel_id}`", inline=True)
        add_field(embed, "Before", "*Unavailable — the original message wasn't in the bot's cache*")
        add_field(embed, "After", payload.data.get("content") or "*(no text)*")
        jump = f"https://discord.com/channels/{guild.id}/{payload.channel_id}/{payload.message_id}"
        add_field(embed, "Message", f"[Jump to message]({jump})", inline=True)
        self.send(guild, "message", embed)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not self.enabled(guild):
            return
        assert guild is not None
        message = payload.cached_message
        channel = guild.get_channel_or_thread(payload.channel_id)

        if message is not None and message.author.bot:
            # Other bots' self-cleanup is noise; but deleting the logger's own log entries is tampering.
            if not (message.author.id == self.bot.user.id and self.router.is_log_channel(channel)):  # type: ignore[union-attr]
                return

        embed = make_embed("message", "🗑️ Message Deleted", colour=discord.Colour.red())
        add_field(embed, "Channel", channel_text(channel) if channel else f"`{payload.channel_id}`", inline=True)
        if message is None:
            add_field(embed, "Author", UNAVAILABLE, inline=True)
            add_field(embed, "Content", "*Unavailable — the message wasn't in the bot's cache (sent before the bot "
                                        "started, or too old). Discord doesn't provide deleted content.*")
            embed.set_footer(text=f"Message ID {payload.message_id}")
            self.send(guild, "message", embed)
            return

        add_field(embed, "Author", user_text(message.author), inline=True)
        add_field(embed, "Content", message.content or "*(no text content)*")
        if message.attachments:
            add_field(
                embed,
                f"📎 Attachments ({len(message.attachments)})",
                join_lines(f"`{a.filename}` ({a.size / 1024:.1f} KB)" for a in message.attachments)
                + "\n*Files can't be recovered after deletion.*",
            )
        if message.stickers:
            add_field(embed, "Stickers", ", ".join(s.name for s in message.stickers))
        add_field(embed, "Sent", time_text(message.created_at), inline=True)

        # The audit log only records deletions of *other people's* messages, and merges repeats
        # into an older entry, so a missing entry means "unknown", never "the author".
        entry = await self.audit.find(
            guild,
            A.message_delete,
            message.author.id,
            predicate=lambda e: getattr(getattr(e.extra, "channel", None), "id", None) == payload.channel_id,
            wait=1.5,
        )
        self.actor_field(
            embed, guild, entry, "Deleted by",
            missing="Unknown *(probably the author; Discord only audit-logs deletions by others)*",
        )  # fmt: skip
        embed.set_footer(text=f"Message ID {payload.message_id}")
        self.send(guild, "message", embed)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not self.enabled(guild):
            return
        assert guild is not None
        channel = guild.get_channel_or_thread(payload.channel_id)
        cached = sorted(payload.cached_messages, key=lambda m: m.created_at)

        entry = await self.audit.find(guild, A.message_bulk_delete, payload.channel_id)
        embed = make_embed("message", "🧹 Messages Bulk Deleted", colour=discord.Colour.red())
        add_field(embed, "Channel", channel_text(channel) if channel else f"`{payload.channel_id}`", inline=True)
        add_field(embed, "Messages deleted", len(payload.message_ids), inline=True)
        add_field(embed, "Content recovered", f"{len(cached)} of {len(payload.message_ids)} (from cache)", inline=True)
        self.actor_field(embed, guild, entry, "Deleted by")

        files = []
        if cached:
            lines = []
            for m in cached:
                attachments = f" [attachments: {', '.join(a.filename for a in m.attachments)}]" if m.attachments else ""
                lines.append(f"[{m.created_at:%Y-%m-%d %H:%M:%S} UTC] {m.author} ({m.author.id}): {m.content}{attachments}")
            stamp = discord.utils.utcnow().strftime("%Y%m%d-%H%M%S")
            files.append(LogFile(f"bulk-delete-{payload.channel_id}-{stamp}.txt", "\n".join(lines).encode("utf-8")))
            add_field(embed, "Transcript", "Recovered messages are attached as a text file.")
        self.send(guild, "message", embed, files)

    # --- roles -----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        if not self.enabled(role.guild):
            return
        entry = await self.audit.find(role.guild, A.role_create, role.id)
        embed = make_embed("role", "🔐 Role Created", colour=discord.Colour.green())
        add_field(embed, "Role", f"{role.mention} (`{role.name}` · `{role.id}`)")
        if role.managed:
            add_field(embed, "Managed", "Yes *(created by a bot or integration)*", inline=True)
        self.actor_field(embed, role.guild, entry, "Created by")
        self.send(role.guild, "role", embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if not self.enabled(role.guild):
            return
        entry = await self.audit.find(role.guild, A.role_delete, role.id)
        embed = make_embed("role", "🔐 Role Deleted", colour=discord.Colour.red())
        add_field(embed, "Role", f"`{role.name}` (`{role.id}`)")
        add_field(embed, "Members who had it", len(role.members), inline=True)
        self.actor_field(embed, role.guild, entry, "Deleted by")
        self.send(role.guild, "role", embed)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        if not self.enabled(after.guild):
            return
        lines = diff_attrs(before, after, ROLE_ATTRS)
        granted, revoked = permission_changes(before.permissions, after.permissions)
        if not lines and not granted and not revoked:
            return  # position-only changes cascade across many roles; skip them.
        entry = await self.audit.find(after.guild, A.role_update, after.id)
        embed = make_embed("role", "🔐 Role Updated")
        add_field(embed, "Role", f"{after.mention} (`{after.name}`)")
        if lines:
            add_field(embed, "Changes", join_lines(lines))
        if granted:
            add_field(embed, "✅ Permissions granted", ", ".join(granted))
        if revoked:
            add_field(embed, "❌ Permissions revoked", ", ".join(revoked))
        self.actor_field(embed, after.guild, entry, "Updated by")
        self.send(after.guild, "role", embed)

    # --- channels --------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        if not self.enabled(channel.guild):
            return
        entry = await self.audit.find(channel.guild, A.channel_create, channel.id)
        embed = make_embed("channel", "📁 Channel Created", colour=discord.Colour.green())
        add_field(embed, "Channel", channel_text(channel))
        add_field(embed, "Type", pretty(channel.type.name), inline=True)
        add_field(embed, "Category", channel.category.name if channel.category else "*None*", inline=True)
        self.actor_field(embed, channel.guild, entry, "Created by")
        self.send(channel.guild, "channel", embed)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if self.router.is_log_channel(channel) or isinstance(channel, discord.CategoryChannel):
            self.router.invalidate(channel.guild.id)
        enabled = self.enabled(channel.guild)  # checked before forgetting, so deleting a log channel is still logged
        self.bot.log_channels.forget_channel(channel.guild.id, channel.id)
        if not enabled:
            return
        entry = await self.audit.find(channel.guild, A.channel_delete, channel.id)
        embed = make_embed("channel", "📁 Channel Deleted", colour=discord.Colour.red())
        add_field(embed, "Channel", f"`{channel.name}` (`{channel.id}`)")
        add_field(embed, "Type", pretty(channel.type.name), inline=True)
        add_field(embed, "Category", channel.category.name if channel.category else "*None*", inline=True)
        self.actor_field(embed, channel.guild, entry, "Deleted by")
        self.send(channel.guild, "channel", embed)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        if not self.enabled(after.guild):
            return
        lines = diff_attrs(before, after, CHANNEL_ATTRS)
        overwrite_lines = overwrite_changes(before.overwrites, after.overwrites)
        if not lines and not overwrite_lines:
            return  # position-only changes
        is_stats_rename = (
            self.bot.log_channels.is_counter(after)
            and not overwrite_lines
            and all(line.startswith("**Name:**") for line in lines)
        )
        if is_stats_rename:
            return  # the stats counters renaming themselves

        actions = {A.channel_update}
        if overwrite_lines:
            actions |= audit_actions("overwrite_create", "overwrite_update", "overwrite_delete")
        entry = await self.audit.find(after.guild, actions, after.id)
        title = "📁 Channel Permissions Updated" if overwrite_lines and not lines else "📁 Channel Updated"
        embed = make_embed("channel", title)
        add_field(embed, "Channel", channel_text(after))
        if lines:
            add_field(embed, "Changes", join_lines(lines))
        if overwrite_lines:
            add_field(embed, "Permission overrides", join_lines(overwrite_lines))
        self.actor_field(embed, after.guild, entry, "Updated by")
        self.send(after.guild, "channel", embed)

    # --- voice -----------------------------------------------------------------

    def _sync_voice_sessions(self) -> None:
        """Align tracked sessions with who is actually in voice (at startup and after reconnects).

        Members already connected when the bot started get the startup time as their start,
        and their durations are shown as a minimum ("at least").
        """
        now = discord.utils.utcnow()
        connected: set[tuple[int, int]] = set()
        for guild in self.bot.guilds:
            for channel in (*guild.voice_channels, *guild.stage_channels):
                for member in channel.members:
                    key = (guild.id, member.id)
                    connected.add(key)
                    session = self._voice_sessions.get(key)
                    if session is None or session.channel_id != channel.id:
                        self._voice_sessions[key] = VoiceSession(channel.id, now, now, approximate=True)
        for key in set(self._voice_sessions) - connected:
            del self._voice_sessions[key]  # left while the bot was disconnected

    def _track_voice(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> tuple[str | None, str | None]:
        """Update the member's voice session. Returns (time in previous channel, whole-session time)."""
        key = (member.guild.id, member.id)
        now = discord.utils.utcnow()
        session = self._voice_sessions.pop(key, None)

        channel_time = session_time = None
        if before.channel is not None and session is not None:
            prefix = "at least " if session.approximate else ""
            channel_time = prefix + format_duration(now - session.channel_since)
            session_time = ("at least " if session.session_approximate else "") + format_duration(now - session.session_since)

        if after.channel is not None:
            if before.channel is None or session is None:
                self._voice_sessions[key] = VoiceSession(after.channel.id, now, now)
            else:  # moved: new channel timer, same session
                self._voice_sessions[key] = VoiceSession(
                    after.channel.id, now, session.session_since, session_approximate=session.session_approximate
                )
        return channel_time, session_time

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        guild = member.guild
        if before.channel != after.channel:
            # Track even when logging is off, so durations are right once it's enabled.
            channel_time, session_time = self._track_voice(member, before, after)
        if not self.enabled(guild):
            return

        if before.channel != after.channel:
            unknown = "Unknown *(joined before the bot started tracking)*"
            if before.channel is None:
                embed = make_embed("voice", "🔊 Joined Voice", colour=discord.Colour.green())
                add_field(embed, "Channel", channel_text(after.channel), inline=True)
            elif after.channel is None:
                embed = make_embed("voice", "🔇 Left Voice", colour=discord.Colour.dark_orange())
                add_field(embed, "Channel", channel_text(before.channel), inline=True)
                add_field(embed, "⏱️ Time in channel", channel_time or unknown, inline=True)
                if session_time and session_time != channel_time:
                    add_field(embed, "⏱️ Total time in voice", session_time, inline=True)
            else:
                embed = make_embed("voice", "🔀 Moved Voice Channel")
                add_field(embed, "From", channel_text(before.channel), inline=True)
                add_field(embed, "To", channel_text(after.channel), inline=True)
                add_field(embed, f"⏱️ Time in {before.channel.name}", channel_time or unknown, inline=True)
            embed.insert_field_at(0, name="Member", value=user_text(member), inline=False)
            self.send(guild, "voice", embed)
            return

        changes = [
            f"**{label}:** {'On' if getattr(after, attr) else 'Off'}"
            for attr, label in VOICE_FLAGS.items()
            if getattr(before, attr) != getattr(after, attr)
        ]
        if not changes:
            return
        embed = make_embed("voice", "🎙️ Voice State Updated")
        add_field(embed, "Member", user_text(member))
        add_field(embed, "Channel", channel_text(after.channel), inline=True)
        add_field(embed, "Changes", join_lines(changes))
        if before.mute != after.mute or before.deaf != after.deaf:
            entry = await self.audit.find(
                guild, A.member_update, member.id, predicate=lambda e: bool({"mute", "deaf"} & changed_keys(e))
            )
            self.actor_field(embed, guild, entry, "Server mute/deafen by")
        self.send(guild, "voice", embed)

    # --- server ----------------------------------------------------------------

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        if not self.enabled(after):
            return
        if (
            before.premium_subscription_count != after.premium_subscription_count
            or before.premium_tier != after.premium_tier
        ):
            embed = make_embed("server", "🚀 Server Boosts Changed", colour=discord.Colour.fuchsia())
            add_field(
                embed, "Boosts", f"{before.premium_subscription_count or 0} → {after.premium_subscription_count or 0}", inline=True
            )
            add_field(embed, "Boost level", f"{before.premium_tier} → {after.premium_tier}", inline=True)
            self.send(after, "server", embed)

        lines = diff_attrs(before, after, GUILD_ATTRS)
        if not lines:
            return
        entry = await self.audit.find(after, A.guild_update, after.id)
        embed = make_embed("server", "⚙️ Server Settings Updated")
        add_field(embed, "Changes", join_lines(lines))
        self.actor_field(embed, after, entry, "Updated by")
        if before.icon != after.icon and after.icon:
            embed.set_thumbnail(url=after.icon.url)
        self.send(after, "server", embed)

    # --- invites ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            return
        if guild.id in self._invites:
            self._invites[guild.id][invite.code] = invite.uses or 0
        if not self.enabled(guild):
            return
        embed = make_embed("invite", "🔗 Invite Created", colour=discord.Colour.green())
        add_field(embed, "Invite", f"`{invite.code}`", inline=True)
        add_field(embed, "Channel", channel_text(invite.channel), inline=True)
        add_field(embed, "Created by", user_text(invite.inviter) if invite.inviter else None, inline=True)
        add_field(embed, "Max uses", invite.max_uses or "Unlimited", inline=True)
        expires = "Never" if not invite.max_age else time_text(discord.utils.utcnow() + datetime.timedelta(seconds=invite.max_age))
        add_field(embed, "Expires", expires, inline=True)
        add_field(embed, "Temporary membership", "Yes" if invite.temporary else "No", inline=True)
        self.send(guild, "invite", embed)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            return
        self._invites.get(guild.id, {}).pop(invite.code, None)
        if not self.enabled(guild):
            return
        entry = await self.audit.find(
            guild,
            A.invite_delete,
            None,
            predicate=lambda e: invite.code in (getattr(e.target, "code", None), getattr(e.before, "code", None)),
        )
        embed = make_embed("invite", "🔗 Invite Deleted", colour=discord.Colour.red())
        add_field(embed, "Invite", f"`{invite.code}`", inline=True)
        add_field(embed, "Channel", channel_text(invite.channel), inline=True)
        self.actor_field(embed, guild, entry, "Deleted by", missing="Unknown *(it may have expired or run out of uses)*")
        self.send(guild, "invite", embed)


async def setup(bot: LoggerBot) -> None:
    await bot.add_cog(LoggingEvents(bot))
