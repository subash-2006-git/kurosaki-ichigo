import re
from datetime import timedelta

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)

MAX_TIMEOUT = timedelta(days=28)


def parse_duration(text: str) -> timedelta | None:
    """Parse strings like '10m', '1h30m', '7d' into a timedelta. Returns None if invalid."""
    text = text.strip().replace(" ", "")
    if not text:
        return None
    matches = DURATION_RE.findall(text)
    # Reject leftovers like '10x' or 'abc' that the regex didn't consume.
    if not matches or "".join(f"{n}{u}" for n, u in matches).lower() != text.lower():
        return None
    seconds = sum(int(n) * UNITS[u.lower()] for n, u in matches)
    return timedelta(seconds=seconds) if seconds > 0 else None


def humanize(seconds: int | float | None) -> str:
    if not seconds:
        return "permanent"
    seconds = int(seconds)
    parts = []
    for name, size in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value} {name}{'s' if value != 1 else ''}")
    return ", ".join(parts[:2])
