"""In-memory per-key cooldowns. A restart resets them, which is harmless."""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable

# Entries older than this are forgotten when the table is pruned (longer than any allowed cooldown).
FORGET_AFTER = 3600
PRUNE_AT = 10_000


class Cooldowns:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last: dict[Hashable, float] = {}

    def try_acquire(self, key: Hashable, seconds: float) -> bool:
        """True (and start a new cooldown) if key isn't cooling down; False otherwise."""
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < seconds:
            return False
        self._last[key] = now
        if len(self._last) > PRUNE_AT:
            self._prune(now)
        return True

    def reset(self, key: Hashable) -> None:
        self._last.pop(key, None)

    def _prune(self, now: float) -> None:
        self._last = {key: last for key, last in self._last.items() if now - last < FORGET_AFTER}

    def __len__(self) -> int:
        return len(self._last)
