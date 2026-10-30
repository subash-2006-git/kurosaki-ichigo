"""Welcome, goodbye and level-up messages as embeds: defaults, placeholders, and link buttons."""

from __future__ import annotations

from typing import Any, Literal

import discord

from core.ui.embed_draft import DraftError, EmbedDraft, validate_url
from features.community.services.guild_config import GuildConfig
from features.community.utils.placeholders import render

MessageKind = Literal["welcome", "goodbye", "levelup"]
MAX_LINK_BUTTONS = 5
BUTTON_LABEL_LIMIT = 80

DEFAULT_EMBEDS: dict[str, dict[str, Any]] = {
    "welcome": {
        "title": "👋 Welcome to {server}!",
        "description": "Hey {user}, we're glad you're here!\nYou're our **{membercount_ordinal}** member. "
                       "Say hi and make yourself at home.",
        "thumbnail_url": "{avatar}",
        "timestamp": True,
        "message": "{user}",
    },
    "goodbye": {
        "title": "👋 Goodbye, {displayname}",
        "description": "**{username}** has left {server}.\nWe're now **{membercount}** members.",
        "thumbnail_url": "{avatar}",
        "timestamp": True,
    },
    "levelup": {
        "title": "🎉 Level up!",
        "description": "{user} just reached **Level {level}**! Keep it up.",
        "thumbnail_url": "{avatar}",
    },
}


def stored_draft(config: GuildConfig, kind: MessageKind) -> EmbedDraft:
    """The server's saved embed for this message, or the built-in default."""
    saved = getattr(config, f"{kind}_embed")
    return EmbedDraft.from_dict(saved if saved else DEFAULT_EMBEDS[kind])


def is_default(config: GuildConfig, kind: MessageKind) -> bool:
    return not getattr(config, f"{kind}_embed")


def fill(draft: EmbedDraft, member: discord.Member, *, level: int | None = None) -> EmbedDraft:
    """The draft with {placeholders} filled in for this member."""
    return draft.rendered(lambda text: render(text, member, level=level))


def link_buttons(config: GuildConfig, kind: MessageKind) -> list[dict[str, str]]:
    return list(getattr(config, f"{kind}_buttons") or [])


def add_link_buttons(view: discord.ui.View, buttons: list[dict[str, str]], *, row: int = 1) -> discord.ui.View:
    for button in buttons[:MAX_LINK_BUTTONS]:
        view.add_item(discord.ui.Button(
            label=button["label"], url=button["url"], emoji=button.get("emoji") or None, row=row,
        ))
    return view


def link_view(buttons: list[dict[str, str]]) -> discord.ui.View | None:
    """A view of link buttons (links need no callbacks, so nothing has to survive restarts)."""
    if not buttons:
        return None
    return add_link_buttons(discord.ui.View(timeout=None), buttons, row=0)


def parse_buttons(text: str) -> list[dict[str, str]]:
    """Parse the editor's one-button-per-line format: `Label | https://link` (optionally `emoji Label | link`).

    Raises DraftError (a ValueError) with a friendly message for anything Discord would reject.
    """
    buttons: list[dict[str, str]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if "|" not in line:
            raise DraftError(f"Line {number}: write it as `Label | https://link`.")
        label, url = (part.strip() for part in line.split("|", 1))
        url = validate_url(url, f"Line {number}'s link")
        if not url:
            raise DraftError(f"Line {number}: add a link after the |.")
        emoji = ""
        first, _, rest = label.partition(" ")
        if rest and not any(ch.isalnum() for ch in first):  # a leading emoji like "📜 Rules"
            emoji, label = first, rest.strip()
        if not label:
            raise DraftError(f"Line {number}: the button needs a label.")
        if len(label) > BUTTON_LABEL_LIMIT:
            raise DraftError(f"Line {number}: labels can be at most {BUTTON_LABEL_LIMIT} characters.")
        buttons.append({"label": label, "url": url, "emoji": emoji})
    if len(buttons) > MAX_LINK_BUTTONS:
        raise DraftError(f"At most {MAX_LINK_BUTTONS} link buttons fit under a message.")
    return buttons


def buttons_text(buttons: list[dict[str, str]]) -> str:
    """The editor's text form of saved buttons (the inverse of parse_buttons)."""
    return "\n".join(f"{(b.get('emoji') + ' ') if b.get('emoji') else ''}{b['label']} | {b['url']}" for b in buttons)
