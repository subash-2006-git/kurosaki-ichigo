"""Channel and counter layout for the logging feature (from logging-bot config.py)."""

from __future__ import annotations

from core.config import STATS_RECONCILE_INTERVAL, STATS_UPDATE_INTERVAL

__all__ = ["STATS_RECONCILE_INTERVAL", "STATS_UPDATE_INTERVAL"]


# --- Logging channels ------------------------------------------------------------

LOG_CATEGORY_NAME = "📂 LOGGING"

# key -> (channel name, channel topic). Order is the order channels are created in.
LOG_CHANNELS: dict[str, tuple[str, str]] = {
    "member": ("👤・member-logs", "General member updates not covered by the dedicated channels."),
    "join_leave": ("🚪・join-leave-logs", "Member joins and leaves."),
    "nickname": ("🏷️・nickname-logs", "Server nickname changes."),
    "profile": ("🖼️・profile-logs", "Username, avatar, and profile changes."),
    "message": ("💬・message-logs", "Message edits, deletions, bulk deletions, and attachment events."),
    "moderation": ("🛡️・moderation-logs", "Bans, unbans, kicks, timeouts, and other moderation actions."),
    "role": ("🔐・role-logs", "Role creation, deletion, changes, and member role assignments."),
    "channel": ("📁・channel-logs", "Channel creation, deletion, updates, and permission changes."),
    "voice": ("🔊・voice-logs", "Voice joins, leaves, moves, mute, deafen, and other voice-state changes."),
    "server": ("⚙️・server-logs", "Server settings, name/icon changes, and boost-related events."),
    "invite": ("🔗・invite-logs", "Invite creation, deletion, and usage information."),
    "bot": ("🤖・bot-logs", "Bot additions/removals, integration events, and webhook changes."),
    "audit": ("📋・audit-logs", "Administrative audit entries that don't fit another log category."),
    "custom": ("🧩・custom-logs", "Logger startup, errors, warnings, setup results, and internal events."),
}


def log_channel_matches(channel_name: str, key: str) -> bool:
    """True if an existing channel name corresponds to the given log channel key.

    Matches the exact name, or the part after the "・" separator, so channels still match
    if Discord normalises the emoji prefix.
    """
    full_name = LOG_CHANNELS[key][0]
    suffix = full_name.split("・")[-1]
    return channel_name == full_name or channel_name.split("・")[-1] == suffix


# --- Server stats ------------------------------------------------------------------

STATS_CATEGORY_NAME = "📊 SERVER STATS"

# key -> label. Channel names are rendered as "<label>: <value>".
STATS_COUNTERS: dict[str, str] = {
    "total": "👥 Total Users",
    "humans": "👤 Humans",
    "bots": "🤖 Bots",
    "online": "🟢 Online",
    "boosts": "🚀 Server Boosts",
}


def stats_channel_name(key: str, value: int) -> str:
    return f"{STATS_COUNTERS[key]}: {value:,}"


def stats_channel_matches(channel_name: str, key: str) -> bool:
    return channel_name.startswith(f"{STATS_COUNTERS[key]}:")
