"""Moderation actions shared by slash commands, automod, reports and warn escalation.

Each function performs the Discord action, DMs the user and records a case.
Callers are responsible for permission and hierarchy checks.
"""
import logging
from datetime import timedelta

import discord

from features.moderation.utils.checks import bot_hierarchy_error
from features.moderation.utils.modlog import create_case, dm_user
from features.moderation.utils.time import MAX_TIMEOUT

log = logging.getLogger(__name__)


def audit_reason(moderator: discord.abc.User, reason: str | None) -> str:
    return f"{moderator} ({moderator.id}): {reason or 'No reason provided'}"[:512]


async def warn(bot, guild: discord.Guild, member: discord.Member, moderator, reason: str | None, *, escalate=True):
    """Warn a member. Returns (warn_case, escalation_case_or_None)."""
    await dm_user(bot, guild, member, "warn", reason)
    case = await create_case(bot, guild, member, moderator, "warn", reason)
    escalation = await apply_escalation(bot, guild, member) if escalate else None
    return case, escalation


async def apply_escalation(bot, guild: discord.Guild, member: discord.Member):
    """Apply the configured punishment if the member's active warn count hits a threshold."""
    count = await bot.mod_db.count_active_warns(guild.id, member.id)
    threshold = await bot.mod_db.get_threshold(guild.id, count)
    if threshold is None:
        return None
    if bot_hierarchy_error(guild, member):
        log.info("Skipping escalation for %s in %s: hierarchy", member.id, guild.id)
        return None

    reason = f"Automatic: reached {count} warnings"
    seconds = threshold["duration_seconds"]
    try:
        match threshold["action"]:
            case "timeout":
                return await timeout(bot, guild, member, guild.me, reason, timedelta(seconds=seconds or 3600))
            case "kick":
                return await kick(bot, guild, member, guild.me, reason)
            case "ban":
                duration = timedelta(seconds=seconds) if seconds else None
                return await ban(bot, guild, member, guild.me, reason, duration=duration)
    except discord.HTTPException as exc:
        log.warning("Escalation failed for %s in %s: %s", member.id, guild.id, exc)
    return None


async def timeout(bot, guild, member: discord.Member, moderator, reason, duration: timedelta):
    duration = min(duration, MAX_TIMEOUT)
    await member.timeout(duration, reason=audit_reason(moderator, reason))
    seconds = int(duration.total_seconds())
    await dm_user(bot, guild, member, "timeout", reason, seconds)
    return await create_case(bot, guild, member, moderator, "timeout", reason, seconds)


async def untimeout(bot, guild, member: discord.Member, moderator, reason):
    await member.timeout(None, reason=audit_reason(moderator, reason))
    await dm_user(bot, guild, member, "untimeout", reason)
    return await create_case(bot, guild, member, moderator, "untimeout", reason)


async def kick(bot, guild, member: discord.Member, moderator, reason):
    # DM before kicking, since we lose the shared server afterwards.
    await dm_user(bot, guild, member, "kick", reason)
    await member.kick(reason=audit_reason(moderator, reason))
    return await create_case(bot, guild, member, moderator, "kick", reason)


async def ban(bot, guild, user: discord.abc.User, moderator, reason, *, duration: timedelta | None = None,
              delete_message_days: int = 0):
    action = "tempban" if duration else "ban"
    seconds = int(duration.total_seconds()) if duration else None
    await dm_user(bot, guild, user, action, reason, seconds)
    await guild.ban(user, reason=audit_reason(moderator, reason), delete_message_seconds=delete_message_days * 86400)
    # A new ban replaces any pending unban from a previous tempban.
    await bot.mod_db.clear_scheduled(guild.id, user.id, "unban")
    if duration:
        await bot.mod_db.add_scheduled(guild.id, user.id, "unban", discord.utils.utcnow() + duration)
    return await create_case(bot, guild, user, moderator, action, reason, seconds)


async def softban(bot, guild, member: discord.Member, moderator, reason, delete_message_days: int = 1):
    await dm_user(bot, guild, member, "softban", reason)
    await guild.ban(member, reason=audit_reason(moderator, reason), delete_message_seconds=delete_message_days * 86400)
    await guild.unban(member, reason=audit_reason(moderator, "Softban: removing ban"))
    return await create_case(bot, guild, member, moderator, "softban", reason)


async def unban(bot, guild, user: discord.abc.User, moderator, reason):
    await guild.unban(user, reason=audit_reason(moderator, reason))
    await bot.mod_db.clear_scheduled(guild.id, user.id, "unban")
    return await create_case(bot, guild, user, moderator, "unban", reason)
