"""Anti-nuke: watch the audit log, stop anyone who goes over a limit, undo what they did, alert staff."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from core.ui import style
from core.ui.theme import brand
from features.antinuke.detector import (
    ACTION_LABELS,
    ACTIONS,
    SNAPSHOT_TTL,
    Tracker,
    channel_snapshot,
    dangerous_permissions_added,
    is_trusted,
    restore_overwrites,
    role_snapshot,
)

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

REASON = "Anti-nuke"
UNDO_HORIZON = 120  # seconds of an attacker's recent actions that get undone
PUNISHMENTS = {"strip_roles": "Removed their roles", "kick": "Kicked them", "ban": "Banned them"}


class AntiNuke(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.tracker = Tracker()
        self.channel_snaps: dict[int, tuple[float, dict[str, Any]]] = {}
        self.role_snaps: dict[int, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- snapshots of deleted things, so they can be recreated ------------------------

    def _prune(self) -> None:
        now = time.monotonic()
        for snaps in (self.channel_snaps, self.role_snaps):
            for key in [k for k, (at, _) in snaps.items() if now - at > SNAPSHOT_TTL]:
                del snaps[key]

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        self._prune()
        self.channel_snaps[channel.id] = (time.monotonic(), channel_snapshot(channel))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        self._prune()
        self.role_snaps[role.id] = (time.monotonic(), role_snapshot(role))

    # --- detection --------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry) -> None:
        guild = entry.guild
        settings = await self.bot.antinuke_db.settings(guild.id)
        if not settings.enabled or entry.user_id is None:
            return
        actor_id = entry.user_id
        actor = guild.get_member(actor_id)
        role_ids = {r.id for r in actor.roles} if actor else set()
        if is_trusted(actor_id, role_ids, owner_id=guild.owner_id, bot_id=self.bot.user.id, settings=settings):
            return
        name = entry.action.name
        target_id = getattr(entry.target, "id", None)

        if name == "bot_add" and settings.block_bot_add:
            await self._remove_added_bot(guild, actor_id, target_id)
        elif name == "role_update" and settings.revert_dangerous_perms:
            await self._check_role_update(guild, entry, actor_id, target_id)
        elif (action := ACTIONS.get(name)) is not None:
            if self.tracker.add(guild.id, actor_id, action, target_id, settings.limits[action]) \
                    and self.tracker.claim(guild.id, actor_id):
                async with self._locks[guild.id]:
                    await self._respond(guild, actor_id, action, settings)

    async def _remove_added_bot(self, guild: discord.Guild, actor_id: int, bot_id: int | None) -> None:
        added = guild.get_member(bot_id) if bot_id else None
        if added is None or not added.bot:
            return
        try:
            await added.kick(reason=f"{REASON}: added by an untrusted member ({actor_id})")
            result = f"{style.OK} Removed the bot {added.mention}."
        except discord.HTTPException:
            result = f"{style.FAIL} Couldn't remove the bot {added.mention}: it's above my role."
        await self._alert(guild, "🤖 Untrusted bot added", f"<@{actor_id}> added a bot.\n{result}",
                          footer="Add trusted people in the panel's Anti-nuke section to allow them to add bots.")

    async def _check_role_update(self, guild: discord.Guild, entry: discord.AuditLogEntry, actor_id: int,
                                 role_id: int | None) -> None:
        before = getattr(entry.before, "permissions", None)
        after = getattr(entry.after, "permissions", None)
        if before is None or after is None:
            return
        added = dangerous_permissions_added(before, after)
        role = guild.get_role(role_id) if role_id else None
        if not added or role is None:
            return
        pretty = ", ".join(p.replace("_", " ").title() for p in added)
        try:
            await role.edit(permissions=before, reason=f"{REASON}: dangerous permissions added by {actor_id}")
            result = f"{style.OK} Reverted {role.mention} to its old permissions."
        except discord.HTTPException:
            result = f"{style.FAIL} Couldn't revert {role.mention}: it's above my role."
        await self._alert(guild, "🛡️ Dangerous permissions blocked", f"<@{actor_id}> gave {role.mention} **{pretty}**.\n{result}")

    # --- response ---------------------------------------------------------------------

    async def _respond(self, guild: discord.Guild, actor_id: int, action: str, settings) -> None:
        log.warning("Anti-nuke tripped in guild %s by %s (%s)", guild.id, actor_id, action)
        done: list[str] = []
        failed: list[str] = []
        await self._punish(guild, actor_id, settings.punishment, done, failed)
        if settings.restore:
            await self._undo(guild, actor_id, done, failed)
        limit = settings.limits[action]
        await self._alert(
            guild, "🧨 Anti-nuke stopped someone",
            f"<@{actor_id}> went over the limit for **{ACTION_LABELS[action].lower()}** "
            f"({limit['count']} in {limit['seconds']}s).",
            done=done, failed=failed,
        )

    async def _punish(self, guild: discord.Guild, actor_id: int, punishment: str, done: list[str], failed: list[str]) -> None:
        member = guild.get_member(actor_id)
        if member is None:
            failed.append("They're not in the server anymore (a webhook or an account that left).")
            return
        if member.top_role >= guild.me.top_role:
            failed.append(f"{member.mention}'s highest role is above mine, so I can't stop them. Move my role to the top.")
            return
        reason = f"{REASON}: went over a limit"
        try:
            if punishment == "ban":
                await guild.ban(member, reason=reason, delete_message_seconds=0)
            elif punishment == "kick" or member.bot:
                await member.kick(reason=reason)
                punishment = "kick"
            else:
                removable = [r for r in member.roles if not r.is_default() and not r.managed and r < guild.me.top_role]
                await member.remove_roles(*removable, reason=reason)
            done.append(f"{PUNISHMENTS[punishment]} ({member.mention}).")
        except discord.HTTPException as exc:
            failed.append(f"Couldn't punish {member.mention}: {exc.text or exc.status}.")

    async def _undo(self, guild: discord.Guild, actor_id: int, done: list[str], failed: list[str]) -> None:
        recent = self.tracker.recent(guild.id, actor_id, UNDO_HORIZON)
        reason = f"{REASON}: undoing {actor_id}"

        for action, getter in (("channel_create", guild.get_channel), ("role_create", guild.get_role)):
            for target_id in recent.get(action, []):
                thing = getter(target_id)
                if thing is not None:
                    try:
                        await thing.delete(reason=reason)
                        done.append(f"Deleted the {'channel' if action == 'channel_create' else 'role'} **{thing.name}** they created.")
                    except discord.HTTPException:
                        failed.append(f"Couldn't delete **{thing.name}**.")

        if recent.get("webhook_create"):
            try:
                hooks = {w.id: w for w in await guild.webhooks()}
                for hook_id in recent["webhook_create"]:
                    if hook_id in hooks:
                        await hooks[hook_id].delete(reason=reason)
                        done.append(f"Deleted the webhook **{hooks[hook_id].name}**.")
            except discord.HTTPException:
                failed.append("Couldn't check webhooks (I need Manage Webhooks).")

        role_map: dict[int, int] = {}
        for old_id in recent.get("role_delete", []):
            snap = self.role_snaps.pop(old_id, (0, None))[1]
            if snap is None:
                failed.append("A deleted role couldn't be restored (no snapshot).")
                continue
            try:
                role = await guild.create_role(
                    name=snap["name"], permissions=discord.Permissions(snap["permissions"]),
                    colour=discord.Colour(snap["colour"]), hoist=snap["hoist"], mentionable=snap["mentionable"], reason=reason,
                )
                role_map[old_id] = role.id
                try:
                    await role.edit(position=max(1, min(snap["position"], guild.me.top_role.position - 1)))
                except discord.HTTPException:
                    pass
                done.append(f"Recreated the role {role.mention}.")
            except discord.HTTPException:
                failed.append(f"Couldn't recreate the role **{snap['name']}**.")

        deleted = [self.channel_snaps.pop(cid, (0, None))[1] | {"old_id": cid}
                   for cid in recent.get("channel_delete", []) if cid in self.channel_snaps]
        missing = len(recent.get("channel_delete", [])) - len(deleted)
        if missing:
            failed.append(f"{missing} deleted channel(s) couldn't be restored (no snapshot).")
        category_map: dict[int, int] = {}
        for snap in sorted(deleted, key=lambda s: (s["kind"] != "category", s["position"])):
            channel = await self._recreate_channel(guild, snap, category_map, role_map, reason)
            if channel is None:
                failed.append(f"Couldn't recreate **#{snap['name']}** ({snap['kind']} channels aren't supported).")
                continue
            if snap["kind"] == "category":
                category_map[snap["old_id"]] = channel.id
            done.append(f"Recreated {channel.mention}.")

        for user_id in recent.get("ban", []):
            try:
                await guild.unban(discord.Object(user_id), reason=reason)
                done.append(f"Unbanned <@{user_id}>.")
            except discord.HTTPException:
                pass

    async def _recreate_channel(self, guild: discord.Guild, snap: dict[str, Any], category_map: dict[int, int],
                                role_map: dict[int, int], reason: str) -> discord.abc.GuildChannel | None:
        overwrites = restore_overwrites(guild, snap["overwrites"], role_map)
        category_id = category_map.get(snap["category_id"], snap["category_id"])
        category = guild.get_channel(category_id) if category_id else None
        category = category if isinstance(category, discord.CategoryChannel) else None
        try:
            if snap["kind"] == "category":
                return await guild.create_category(snap["name"], overwrites=overwrites, position=snap["position"], reason=reason)
            if snap["kind"] == "voice":
                return await guild.create_voice_channel(
                    snap["name"], category=category, overwrites=overwrites, position=snap["position"],
                    bitrate=min(snap.get("bitrate") or 64000, int(guild.bitrate_limit)), user_limit=snap.get("user_limit") or 0,
                    reason=reason,
                )
            if snap["kind"] in ("text", "news"):
                return await guild.create_text_channel(
                    snap["name"], category=category, overwrites=overwrites, position=snap["position"],
                    topic=snap.get("topic"), nsfw=snap.get("nsfw", False), slowmode_delay=snap.get("slowmode_delay", 0),
                    reason=reason,
                )
        except discord.HTTPException as exc:
            log.warning("Anti-nuke couldn't recreate #%s in guild %s: %s", snap["name"], guild.id, exc)
        return None

    # --- alert ------------------------------------------------------------------------

    async def _alert_channel(self, guild: discord.Guild, settings) -> discord.abc.Messageable | None:
        candidates = [settings.alert_channel_id]
        mod = getattr(self.bot, "mod_configs", None)  # the moderation feature's alerts / mod-log channels
        if mod is not None:
            mod_settings = await mod.get(guild.id)
            candidates += [mod_settings.alert_channel_id, mod_settings.modlog_channel_id]
        for channel_id in candidates:
            channel = guild.get_channel(channel_id) if channel_id else None
            if isinstance(channel, discord.abc.Messageable):
                return channel
        return None

    async def _alert(self, guild: discord.Guild, title: str, description: str, *, done: list[str] | None = None,
                     failed: list[str] | None = None, footer: str | None = None) -> None:
        settings = await self.bot.antinuke_db.settings(guild.id)
        channel = await self._alert_channel(guild, settings)
        if channel is None:
            log.warning("Anti-nuke alert in guild %s had nowhere to go: %s", guild.id, description)
            return
        embed = discord.Embed(title=title, description=description, colour=style.ERROR, timestamp=discord.utils.utcnow())
        if done:
            embed.add_field(name="✅ Done", value="\n".join(done)[:1024], inline=False)
        if failed:
            embed.add_field(name="⚠️ Couldn't do", value="\n".join(failed)[:1024], inline=False)
        if footer:
            embed.set_footer(text=footer)
        brand(embed, guild)
        try:
            await channel.send(f"<@{guild.owner_id}>", embed=embed,
                               allowed_mentions=discord.AllowedMentions(users=[discord.Object(guild.owner_id)]))
        except discord.HTTPException as exc:
            log.warning("Anti-nuke alert not sent in guild %s: %s", guild.id, exc)


async def setup(bot: Bot) -> None:
    await bot.add_cog(AntiNuke(bot))
