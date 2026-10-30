"""Decide what a member's nickname should become.

Shared by previews, bulk jobs, and the join handler so they always agree.
"""

from dataclasses import dataclass

import discord

from features.nickname.db import GuildSettings, NicknameRecord
from features.nickname.services.nickname_templates import TemplateError, render, strip_template
from features.nickname.services.permission_checks import skip_reason

SKIP_NO_TEMPLATE = "no template configured"
SKIP_INVALID = "resulting nickname is invalid"
SKIP_ALREADY_SET = "already has the nickname"
SKIP_NOT_RENAMED = "never renamed by the bot"
SKIP_CHANGED_SINCE = "changed their nickname since"


@dataclass
class Decision:
    new_nick: str | None = None
    skip_reason: str | None = None
    # The nickname to remember as "before the bot", for /nick reset.
    original_nick: str | None = None

    @property
    def skipped(self) -> bool:
        return self.skip_reason is not None


def account_name(member: discord.Member) -> str:
    """The name Discord shows when the member has no server nickname."""
    return member.global_name or member.name


def resolve_original(member: discord.Member, template: str, record: NicknameRecord | None) -> str | None:
    """Work out the member's nickname from before any template was applied."""
    if record is not None and member.nick == record.applied_nick:
        return record.original_nick
    # Untouched by the bot, or the member picked a new nickname since. Strip the
    # template in case it was applied by hand so the prefix isn't doubled.
    if member.nick is None:
        return None
    stripped = strip_template(template, member.nick)
    return stripped if stripped != account_name(member) else None


def plan_rename(member: discord.Member, settings: GuildSettings, record: NicknameRecord | None) -> Decision:
    if reason := skip_reason(member, settings):
        return Decision(skip_reason=reason)
    if not settings.template:
        return Decision(skip_reason=SKIP_NO_TEMPLATE)

    original = resolve_original(member, settings.template, record)
    try:
        new_nick = render(settings.template, original or account_name(member), member.name)
    except TemplateError:
        return Decision(skip_reason=SKIP_INVALID)

    if new_nick == member.nick:
        return Decision(skip_reason=SKIP_ALREADY_SET)
    return Decision(new_nick=new_nick, original_nick=original)


def plan_reset(member: discord.Member, settings: GuildSettings, record: NicknameRecord | None) -> Decision:
    if reason := skip_reason(member, settings):
        return Decision(skip_reason=reason)
    if record is None:
        return Decision(skip_reason=SKIP_NOT_RENAMED)
    if member.nick != record.applied_nick:
        return Decision(skip_reason=SKIP_CHANGED_SINCE)
    return Decision(new_nick=record.original_nick, original_nick=record.original_nick)


PLANNERS = {"apply": plan_rename, "reset": plan_reset}
