"""Voice XP rules: who is eligible and who is due an award. No Discord objects in here.

The cog turns Discord voice states into VoiceMember values and calls VoiceTracker.collect
once per tick; everything about timing and eligibility is decided here so it can be tested.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from features.community.services.guild_config import GuildConfig

# The loop ticks every 60s but never exactly on time; without some slack a 60s interval
# would often be missed by milliseconds and award only every 120s.
TICK_SLACK = 5.0

Key = tuple[int, int]  # (guild_id, user_id)


@dataclass(frozen=True)
class VoiceMember:
    user_id: int
    bot: bool = False
    deafened: bool = False  # self-deafened or server-deafened
    muted: bool = False  # self-muted, server-muted, or a stage audience member


def present_humans(members: Iterable[VoiceMember], config: GuildConfig) -> list[VoiceMember]:
    """Humans who count as taking part under the guild's mute/deafen rules."""
    return [
        member
        for member in members
        if not member.bot
        and not (config.voice_ignore_deafened and member.deafened)
        and not (config.voice_ignore_muted and member.muted)
    ]


class VoiceTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._joined: dict[Key, float] = {}
        self._last_award: dict[Key, float] = {}

    # ------------------------------------------------------------------ sessions

    def joined(self, guild_id: int, user_id: int) -> None:
        """Start a session (moving between channels doesn't restart it)."""
        self._joined.setdefault((guild_id, user_id), self._clock())

    def left(self, guild_id: int, user_id: int) -> None:
        self._joined.pop((guild_id, user_id), None)
        self._last_award.pop((guild_id, user_id), None)

    def sync_guild(self, guild_id: int, present_user_ids: Iterable[int]) -> None:
        """Match sessions to who is in voice right now (startup and reconnects).

        Members already tracked keep their join time; new ones start now; anyone who
        left while the bot was disconnected is forgotten.
        """
        present = set(present_user_ids)
        for key in [key for key in self._joined if key[0] == guild_id and key[1] not in present]:
            self.left(*key)
        for user_id in present:
            self.joined(guild_id, user_id)

    def session_seconds(self, guild_id: int, user_id: int) -> float | None:
        joined = self._joined.get((guild_id, user_id))
        return None if joined is None else self._clock() - joined

    # ------------------------------------------------------------------ awards

    def collect(self, guild_id: int, channels: Iterable[list[VoiceMember]], config: GuildConfig) -> list[int]:
        """User ids due voice XP now, across the guild's (non-AFK) voice channels.

        Marks them as awarded, so call this once per tick and award everyone it returns.
        """
        now = self._clock()
        due: list[int] = []
        for members in channels:
            present = present_humans(members, config)
            # "Not alone" counts everyone present, even people who only just joined.
            if config.voice_require_others and len(present) < 2:
                continue
            for member in present:
                key = (guild_id, member.user_id)
                # A member we never saw join (e.g. a missed event) starts their session now.
                joined = self._joined.setdefault(key, now)
                if now - joined < config.voice_min_session:
                    continue
                last = self._last_award.get(key)
                if last is not None and now - last < config.voice_xp_interval - TICK_SLACK:
                    continue
                self._last_award[key] = now
                due.append(member.user_id)
        return due
