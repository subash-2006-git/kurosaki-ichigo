import logging

import discord

from features.moderation.utils.embeds import ACTION_COLORS, ACTION_PAST_TENSE, INFO, truncate
from features.moderation.utils.time import humanize

log = logging.getLogger(__name__)


def case_embed(case, *, title_prefix: str = "Case") -> discord.Embed:
    action = case["action"]
    embed = discord.Embed(
        title=f"{title_prefix} #{case['case_num']} | {action.title()}",
        color=ACTION_COLORS.get(action, INFO),
        timestamp=case["created_at"],
    )
    embed.add_field(name="User", value=f"<@{case['user_id']}> (`{case['user_id']}`)", inline=True)
    embed.add_field(name="Moderator", value=f"<@{case['mod_id']}>", inline=True)
    if case["duration_seconds"]:
        embed.add_field(name="Duration", value=humanize(case["duration_seconds"]), inline=True)
    embed.add_field(name="Reason", value=truncate(case["reason"]), inline=False)
    if action == "warn" and not case["active"]:
        embed.set_footer(text="This warning has been removed")
    return embed


async def send_log(bot, guild: discord.Guild, embed: discord.Embed) -> None:
    settings = await bot.mod_configs.get(guild.id)
    if not settings.modlog_channel_id:
        return
    channel = guild.get_channel(settings.modlog_channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException as exc:
        log.warning("Failed to send mod-log in guild %s: %s", guild.id, exc)


async def create_case(
    bot,
    guild: discord.Guild,
    user: discord.abc.Snowflake,
    moderator: discord.abc.Snowflake,
    action: str,
    reason: str | None,
    duration_seconds: int | None = None,
):
    """Record a case in the database and post it to the mod-log channel."""
    case = await bot.mod_db.create_case(guild.id, user.id, moderator.id, action, reason, duration_seconds)
    await send_log(bot, guild, case_embed(case))
    return case


async def dm_user(
    bot,
    guild: discord.Guild,
    user: discord.abc.Snowflake,
    action: str,
    reason: str | None,
    duration_seconds: int | None = None,
) -> bool:
    """DM a user about an action taken against them. Returns True if delivered."""
    if not isinstance(user, (discord.User, discord.Member)) or user.bot:
        return False
    settings = await bot.mod_configs.get(guild.id)
    if not settings.dm_on_action:
        return False

    embed = discord.Embed(
        title=f"You have been {ACTION_PAST_TENSE.get(action, action)} in {guild.name}",
        color=ACTION_COLORS.get(action, INFO),
    )
    embed.add_field(name="Reason", value=truncate(reason), inline=False)
    if duration_seconds:
        embed.add_field(name="Duration", value=humanize(duration_seconds), inline=False)
    if settings.appeal_url and action not in {"unban", "untimeout"}:
        embed.add_field(name="Appeal", value=settings.appeal_url, inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    try:
        await user.send(embed=embed)
        return True
    except discord.HTTPException:
        return False
