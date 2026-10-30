"""Template placeholders for welcome, goodbye and level-up text."""

from __future__ import annotations

import re
from collections.abc import Callable

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
    "{avatar}": "Link to their avatar (for embed images)",
    "{server_icon}": "Link to the server icon (for embed images)",
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
    """Replace known {tokens}; unknown ones are left as typed. Values are only computed when used."""
    values: dict[str, Callable[[], str]] = {
        "user": lambda: member.mention,
        "username": lambda: escape_markdown(member.name),
        "displayname": lambda: escape_markdown(member.display_name),
        "user_id": lambda: str(member.id),
        "server": lambda: escape_markdown(member.guild.name),
        "membercount": lambda: f"{member_count(member.guild):,}",
        "membercount_ordinal": lambda: ordinal(member_count(member.guild)),
        "avatar": lambda: member.display_avatar.url,
        "server_icon": lambda: member.guild.icon.url if member.guild.icon else "",
    }
    if level is not None:
        values["level"] = lambda: str(level)

    def replace(match: re.Match[str]) -> str:
        value = values.get(match.group(1))
        return value() if value is not None else match.group(0)

    return _TOKEN.sub(replace, template)


def help_text(*, include_level: bool = False) -> str:
    items = dict(PLACEHOLDERS, **(LEVEL_PLACEHOLDERS if include_level else {}))
    return "\n".join(f"`{token}` – {description}" for token, description in items.items())
