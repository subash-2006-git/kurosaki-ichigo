"""Anti-nuke decisions, kept free of Discord I/O so they can be tested directly.

- Which audit-log actions count, and their default limits.
- A sliding window per (server, person, action) that says when someone went over the limit.
- Which permission changes are dangerous.
- Snapshots of deleted channels and roles, and how to recreate them.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

import discord

# Audit-log action name -> our action key. Only these count toward limits.
ACTIONS: dict[str, str] = {
    "channel_delete": "channel_delete",
    "channel_create": "channel_create",
    "role_delete": "role_delete",
    "role_create": "role_create",
    "ban": "ban",
    "kick": "kick",
    "webhook_create": "webhook_create",
}

DEFAULT_LIMITS: dict[str, dict[str, int]] = {
    "channel_delete": {"count": 3, "seconds": 10},
    "channel_create": {"count": 5, "seconds": 10},
    "role_delete": {"count": 3, "seconds": 10},
    "role_create": {"count": 5, "seconds": 10},
    "ban": {"count": 3, "seconds": 10},
    "kick": {"count": 3, "seconds": 10},
    "webhook_create": {"count": 3, "seconds": 10},
}

ACTION_LABELS = {
    "channel_delete": "Channel deletes",
    "channel_create": "Channel creates",
    "role_delete": "Role deletes",
    "role_create": "Role creates",
    "ban": "Bans",
    "kick": "Kicks",
    "webhook_create": "Webhook creates",
}

DANGEROUS_PERMISSIONS = (
    "administrator", "ban_members", "kick_members", "manage_roles", "manage_channels",
    "manage_guild", "manage_webhooks", "mention_everyone",
)

SNAPSHOT_TTL = 600  # seconds a deleted channel/role snapshot is kept for restoring


def merged_limits(stored: dict | None) -> dict[str, dict[str, int]]:
    limits = {k: dict(v) for k, v in DEFAULT_LIMITS.items()}
    for key, value in (stored or {}).items():
        if key in limits and isinstance(value, dict):
            limits[key].update({k: int(v) for k, v in value.items() if k in ("count", "seconds")})
    return limits


def parse_limit(text: str, name: str) -> dict[str, int]:
    """'3/10' -> {'count': 3, 'seconds': 10}. Raises ValueError with a friendly message."""
    try:
        count_text, seconds_text = text.replace(" ", "").split("/")
        count, seconds = int(count_text), int(seconds_text)
    except ValueError:
        raise ValueError(f"{name}: write it as count/seconds, like 3/10.") from None
    if not 1 <= count <= 50 or not 1 <= seconds <= 600:
        raise ValueError(f"{name}: count must be 1–50 and seconds 1–600.")
    return {"count": count, "seconds": seconds}


def format_limit(limit: dict[str, int]) -> str:
    return f"{limit['count']}/{limit['seconds']}"


def dangerous_permissions_added(before: discord.Permissions, after: discord.Permissions) -> list[str]:
    return [name for name in DANGEROUS_PERMISSIONS if getattr(after, name) and not getattr(before, name)]


def is_trusted(user_id: int, role_ids: set[int], *, owner_id: int, bot_id: int, settings) -> bool:
    return (
        user_id in (owner_id, bot_id)
        or user_id in settings.trusted_user_ids
        or bool(role_ids & set(settings.trusted_role_ids))
    )


@dataclass
class Hit:
    at: float
    target_id: int | None


@dataclass
class Tracker:
    """Recent actions per (guild, actor, action). `add` says whether this one went over the limit."""

    hits: dict[tuple[int, int, str], deque[Hit]] = field(default_factory=lambda: defaultdict(deque))
    handled_until: dict[tuple[int, int], float] = field(default_factory=dict)

    def add(self, guild_id: int, actor_id: int, action: str, target_id: int | None, limit: dict[str, int],
            now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        window = self.hits[(guild_id, actor_id, action)]
        window.append(Hit(now, target_id))
        while window and now - window[0].at > limit["seconds"]:
            window.popleft()
        return len(window) > limit["count"]

    def recent(self, guild_id: int, actor_id: int, horizon: float, now: float | None = None) -> dict[str, list[int]]:
        """Targets of everything this actor did in the last `horizon` seconds, per action."""
        now = time.monotonic() if now is None else now
        found: dict[str, list[int]] = {}
        for (g, a, action), window in self.hits.items():
            if g == guild_id and a == actor_id:
                targets = [h.target_id for h in window if now - h.at <= horizon and h.target_id is not None]
                if targets:
                    found[action] = targets
        return found

    def claim(self, guild_id: int, actor_id: int, cooldown: float = 60, now: float | None = None) -> bool:
        """True the first time an actor trips; then False for `cooldown` seconds, so one nuke gets one response."""
        now = time.monotonic() if now is None else now
        key = (guild_id, actor_id)
        if self.handled_until.get(key, 0) > now:
            return False
        self.handled_until[key] = now + cooldown
        return True


# ---- snapshots ----------------------------------------------------------------------


def overwrite_snapshot(channel: discord.abc.GuildChannel) -> list[dict[str, int]]:
    out = []
    for target, overwrite in channel.overwrites.items():
        allow, deny = overwrite.pair()
        out.append({"id": target.id, "role": int(isinstance(target, discord.Role)), "allow": allow.value, "deny": deny.value})
    return out


def channel_snapshot(channel: discord.abc.GuildChannel) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "kind": channel.type.name,
        "name": channel.name,
        "position": channel.position,
        "category_id": channel.category_id,
        "overwrites": overwrite_snapshot(channel),
    }
    if isinstance(channel, discord.TextChannel):
        snap.update(topic=channel.topic, nsfw=channel.nsfw, slowmode_delay=channel.slowmode_delay)
    if isinstance(channel, discord.VoiceChannel):
        snap.update(bitrate=channel.bitrate, user_limit=channel.user_limit)
    return snap


def role_snapshot(role: discord.Role) -> dict[str, Any]:
    return {
        "name": role.name,
        "permissions": role.permissions.value,
        "colour": role.colour.value,
        "hoist": role.hoist,
        "mentionable": role.mentionable,
        "position": role.position,
    }


def restore_overwrites(guild: discord.Guild, snapshot: list[dict[str, int]], role_map: dict[int, int]) -> dict:
    """Overwrites for a recreated channel; roles that were also recreated are mapped to their new IDs."""
    out = {}
    for item in snapshot:
        target_id = role_map.get(item["id"], item["id"])
        target = guild.get_role(target_id) if item["role"] else guild.get_member(target_id)
        if target is not None:
            out[target] = discord.PermissionOverwrite.from_pair(
                discord.Permissions(item["allow"]), discord.Permissions(item["deny"])
            )
    return out
