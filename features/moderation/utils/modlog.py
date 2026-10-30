import logging

import discord

from core.ui.theme import brand
from features.moderation.utils.embeds import ACTION_COLORS, ACTION_ICONS, ACTION_LABELS, ACTION_PAST_TENSE, INFO, truncate
from features.moderation.utils.time import humanize

log = logging.getLogger(__name__)


def case_embed(case, *, title_prefix: str = "Case", user: discord.abc.User | None = None) -> discord.Embed:
    action = case["action"]
    icon = ACTION_ICONS.get(action, "📝")
    label = ACTION_LABELS.get(action, action.title())
    title = f"{icon} {label} · Case #{case['case_num']}"
    if title_prefix != "Case":
        title = f"{icon} {title_prefix} #{case['case_num']} · {label}"
    embed = discord.Embed(title=title, color=ACTION_COLORS.get(action, INFO), timestamp=case["created_at"])
    if isinstance(user, (discord.User, discord.Member)):
        embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="User", value=f"<@{case['user_id']}>\n`{case['user_id']}`", inline=True)
    embed.add_field(name="Moderator", value=f"<@{case['mod_id']}>", inline=True)
    if case["duration_seconds"]:
        embed.add_field(name="Duration", value=humanize(case["duration_seconds"]), inline=True)
    embed.add_field(name="Reason", value=f">>> {truncate(case['reason'], 1000)}", inline=False)
    footer = f"Case #{case['case_num']}"
    if action == "warn" and not case["active"]:
        footer += " · this warning has been removed"
    embed.set_footer(text=footer)
    return embed


async def send_log(bot, guild: discord.Guild, embed: discord.Embed, view: discord.ui.View | None = None) -> None:
    settings = await bot.mod_configs.get(guild.id)
    if not settings.modlog_channel_id:
        return
    channel = guild.get_channel(settings.modlog_channel_id)
    if channel is None:
        return
    brand(embed, guild)
    try:
        await channel.send(embed=embed, view=view or discord.utils.MISSING, allowed_mentions=discord.AllowedMentions.none())
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
    """Record a case in the database and post it to the mod-log channel (with History/Undo buttons)."""
    from features.moderation.case_buttons import case_view

    case = await bot.mod_db.create_case(guild.id, user.id, moderator.id, action, reason, duration_seconds)
    await send_log(bot, guild, case_embed(case, user=user), case_view(case))
    return case


def appeal_view(url: str | None) -> discord.ui.View | None:
    if not url or not url.startswith(("http://", "https://")):
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Appeal", emoji="📨", url=url))
    return view


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
        title=f"{ACTION_ICONS.get(action, '📝')} You were {ACTION_PAST_TENSE.get(action, action)} in {guild.name}",
        description=f"**Reason**\n>>> {truncate(reason, 1000)}",
        color=ACTION_COLORS.get(action, INFO),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    if duration_seconds:
        embed.add_field(name="Duration", value=humanize(duration_seconds), inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    brand(embed, guild)
    view = appeal_view(settings.appeal_url) if action not in {"unban", "untimeout"} else None

    try:
        await user.send(embed=embed, view=view or discord.utils.MISSING)
        return True
    except discord.HTTPException:
        return False
