"""Permission helpers for things the bot itself needs to do."""

from __future__ import annotations

import discord

SEND_PERMISSIONS = ("view_channel", "send_messages", "embed_links", "attach_files")


def missing_send_permissions(channel: discord.abc.GuildChannel) -> list[str]:
    permissions = channel.permissions_for(channel.guild.me)
    return [name for name in SEND_PERMISSIONS if not getattr(permissions, name)]


def can_send(channel: discord.abc.GuildChannel) -> bool:
    return not missing_send_permissions(channel)


def format_permissions(names: list[str]) -> str:
    return ", ".join(name.replace("_", " ").replace("guild", "server").title() for name in names)


def resolve_channel(guild: discord.Guild, channel_id: int | None) -> tuple[discord.TextChannel | None, str | None]:
    """Return (channel, problem). problem is a human-readable reason it can't be used."""
    if channel_id is None:
        return None, "No channel set"
    channel = guild.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return None, "Channel was deleted"
    missing = missing_send_permissions(channel)
    if missing:
        return channel, f"I'm missing {format_permissions(missing)} there"
    return channel, None


def channel_problem_reply(channel: discord.TextChannel) -> str | None:
    missing = missing_send_permissions(channel)
    if missing:
        return f"I can't post in {channel.mention}: I'm missing {format_permissions(missing)}."
    return None


def is_at_or_above(role: discord.Role, other: discord.Role) -> bool:
    """Discord's role hierarchy: higher position wins; on equal positions the older role (lower id) is higher.

    Equal positions are common right after the bot creates a role, since both can sit at the bottom.
    """
    if role.position != other.position:
        return role.position > other.position
    return role.id <= other.id


def role_problem(guild: discord.Guild, role: discord.Role) -> str | None:
    """Why the bot can't give or take this role, or None if it can."""
    if role.is_default():
        return "@everyone can't be a level role"
    if role.managed:
        return f"{role.name} is managed by an integration (a bot or boost role)"
    if not guild.me.guild_permissions.manage_roles:
        return "I'm missing the Manage Roles permission"
    if is_at_or_above(role, guild.me.top_role):
        return f"{role.name} is above my highest role; move my role above it in Server Settings → Roles"
    return None
