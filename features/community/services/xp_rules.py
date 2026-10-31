"""XP boost events and no-XP channels: small rules shared by message and voice XP."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def boost_multiplier(config: Any, now: datetime | None = None) -> int:
    """The active XP multiplier: the boost's while it runs, otherwise 1."""
    until = config.xp_boost_until
    if not until or (config.xp_boost_multiplier or 1) <= 1:
        return 1
    now = now or datetime.now(timezone.utc)
    return int(config.xp_boost_multiplier) if until > now else 1


def is_no_xp(channel: Any, config: Any) -> bool:
    """True if this channel, the channel a thread is in, or the channel's category earns no XP."""
    blocked = set(config.no_xp_channel_ids or ())
    if not blocked:
        return False
    ids = {getattr(channel, "id", None), getattr(channel, "parent_id", None), getattr(channel, "category_id", None)}
    parent = getattr(channel, "parent", None)  # a thread's channel can itself be in a blocked category
    if parent is not None:
        ids.add(getattr(parent, "category_id", None))
    return bool(ids & blocked)
