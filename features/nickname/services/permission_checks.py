import discord

from features.nickname.db import GuildSettings

# Skip reasons, shared so reports group them consistently.
SKIP_BOT = "bot account"
SKIP_EXCLUDED_MEMBER = "excluded member"
SKIP_EXCLUDED_ROLE = "has an excluded role"
SKIP_OWNER = "server owner"
SKIP_SELF = "the bot itself"
SKIP_ROLE_HIERARCHY = "role is at or above the bot's"


def bot_permission_problem(guild: discord.Guild) -> str | None:
    """Return a message if the bot can't manage nicknames in this server at all."""
    if not guild.me.guild_permissions.manage_nicknames:
        return "I'm missing the **Manage Nicknames** permission in this server."
    return None


def eligibility_reason(member: discord.Member, settings: GuildSettings) -> str | None:
    """Return why the server's settings exclude this member, or None if eligible."""
    if member.bot and not settings.include_bots:
        return SKIP_BOT
    if member.id in settings.excluded_members:
        return SKIP_EXCLUDED_MEMBER
    if any(role.id in settings.excluded_roles for role in member.roles):
        return SKIP_EXCLUDED_ROLE
    return None


def manageability_reason(member: discord.Member) -> str | None:
    """Return why Discord won't let the bot rename this member, or None if it can."""
    guild = member.guild
    if member.id == guild.owner_id:
        return SKIP_OWNER
    if member.id == guild.me.id:
        return SKIP_SELF
    if member.top_role >= guild.me.top_role:
        return SKIP_ROLE_HIERARCHY
    return None


def skip_reason(member: discord.Member, settings: GuildSettings) -> str | None:
    return eligibility_reason(member, settings) or manageability_reason(member)
