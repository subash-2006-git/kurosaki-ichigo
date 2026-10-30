"""Audit-log attribution: finds who performed an action, without guessing."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable, Iterable

import discord

if TYPE_CHECKING:
    from core.bot import Bot as LoggerBot

log = logging.getLogger(__name__)

Predicate = Callable[[discord.AuditLogEntry], bool]


def audit_actions(*names: str) -> frozenset[discord.AuditLogAction]:
    """Build a set of audit actions by name, skipping any this discord.py version lacks."""
    return frozenset(a for n in names if (a := getattr(discord.AuditLogAction, n, None)) is not None)


def safe_changes(entry: discord.AuditLogEntry) -> list[tuple[str, Any, Any]] | None:
    """(attribute, before, after) triples, or None if discord.py can't decode this entry.

    discord.py decodes changes lazily and can raise on payloads it doesn't expect (e.g. null
    values or action types newer than the library), so never touch entry.changes directly.
    """
    try:
        before, after = dict(entry.before), dict(entry.after)
    except Exception:
        log.debug("Could not decode changes for audit entry %s (%s)", entry.id, entry.action, exc_info=True)
        return None
    return [(k, before.get(k), after.get(k)) for k in {*before, *after}]


def safe_target(entry: discord.AuditLogEntry) -> Any:
    try:
        return entry.target
    except Exception:
        return None


def changed_keys(entry: discord.AuditLogEntry) -> set[str]:
    return {k for k, _, _ in safe_changes(entry) or ()}


class AuditHandler:
    """Caches audit entries pushed over the gateway and matches them to events.

    Gateway events usually arrive slightly before the matching audit entry, so lookups wait
    briefly for the entry, then fall back to one audit-log API request.
    """

    CACHE_PER_GUILD = 200

    def __init__(self, bot: LoggerBot) -> None:
        self.bot = bot
        self._recent: dict[int, deque[discord.AuditLogEntry]] = defaultdict(
            lambda: deque(maxlen=self.CACHE_PER_GUILD)
        )

    def record(self, entry: discord.AuditLogEntry) -> None:
        self._recent[entry.guild.id].appendleft(entry)

    @staticmethod
    def can_view(guild: discord.Guild) -> bool:
        return guild.me.guild_permissions.view_audit_log

    async def find(
        self,
        guild: discord.Guild,
        actions: Iterable[discord.AuditLogAction] | discord.AuditLogAction,
        target_id: int | None,
        *,
        predicate: Predicate | None = None,
        window: float = 15.0,
        wait: float = 2.5,
    ) -> discord.AuditLogEntry | None:
        """Return the matching recent audit entry, or None if it can't be identified reliably."""
        if not self.can_view(guild):
            return None
        action_set = frozenset([actions]) if isinstance(actions, discord.AuditLogAction) else frozenset(actions)

        def matches(entry: discord.AuditLogEntry) -> bool:
            if entry.action not in action_set:
                return False
            if discord.utils.utcnow() - entry.created_at > timedelta(seconds=window):
                return False
            if target_id is not None and getattr(safe_target(entry), "id", None) != target_id:
                return False
            try:
                return predicate is None or predicate(entry)
            except Exception:
                # A malformed entry must never break attribution for other events.
                log.debug("Audit predicate failed for entry %s", entry.id, exc_info=True)
                return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            for entry in self._recent.get(guild.id, ()):
                if matches(entry):
                    return entry
            if loop.time() >= deadline:
                break
            await asyncio.sleep(0.5)

        # Fallback: the entry may not have been pushed over the gateway.
        try:
            # discord.py's `action` must be omitted (not None) to search all action types.
            filters: dict[str, Any] = {"action": next(iter(action_set))} if len(action_set) == 1 else {}
            async for entry in guild.audit_logs(limit=15, **filters):
                if matches(entry):
                    self.record(entry)
                    return entry
        except discord.Forbidden:
            return None
        except discord.HTTPException as exc:
            log.warning("Audit log fetch failed in %s: %s", guild.id, exc)
        return None

    def actor_text(self, guild: discord.Guild, entry: discord.AuditLogEntry | None, *, missing: str | None = None) -> str:
        """Human-readable actor for a log field. Unknown actors are labelled, never guessed."""
        if entry is None:
            if not self.can_view(guild):
                return "Unknown *(bot lacks View Audit Log)*"
            return missing or "Unknown *(not found in the audit log)*"
        user = entry.user or (guild.get_member(entry.user_id) if entry.user_id else None)
        if user is None:
            return f"Unknown user (`{entry.user_id}`)" if entry.user_id else "Unknown"
        return f"{user.mention} (`{user}`)"

    @staticmethod
    def reason_text(entry: discord.AuditLogEntry | None) -> str | None:
        if entry is None:
            return None
        return entry.reason or "*No reason provided*"
