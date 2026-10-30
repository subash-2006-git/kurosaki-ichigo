"""Template placeholders for welcome, goodbye and level-up text."""

from __future__ import annotations

import re

import discord
from discord.utils import escape_markdown

PLACEHOLDERS = {
    "{user}": "Mentions the member",
    "{username}": "Their username",
    "{displayname}": "Their display name in this server",
    "{user_id}": "Their user ID",
    "{server}": "The server name",
    "{membercount}": "Member count, e.g. 1,284",
    "{membercount_ordinal}": "Member count as an ordinal, e.g. 1,284th",
}
LEVEL_PLACEHOLDERS = {"{level}": "The level just reached (level-up text only)"}

_TOKEN = re.compile(r"\{(\w+)\}")


def ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def member_count(guild: discord.Guild) -> int:
    return guild.member_count if guild.member_count is not None else len(guild.members)


def render(template: str, member: discord.Member, *, level: int | None = None) -> str:
    """Replace known {tokens}; unknown ones are left as typed."""
    count = member_count(member.guild)
    values = {
        "user": member.mention,
        "username": escape_markdown(member.name),
        "displayname": escape_markdown(member.display_name),
        "user_id": str(member.id),
        "server": escape_markdown(member.guild.name),
        "membercount": f"{count:,}",
        "membercount_ordinal": ordinal(count),
    }
    if level is not None:
        values["level"] = str(level)

    return _TOKEN.sub(lambda match: values.get(match.group(1), match.group(0)), template)


def help_text(*, include_level: bool = False) -> str:
    items = dict(PLACEHOLDERS, **(LEVEL_PLACEHOLDERS if include_level else {}))
    return "\n".join(f"`{token}` – {description}" for token, description in items.items())
