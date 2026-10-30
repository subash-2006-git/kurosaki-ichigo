"""Repositories: the only place that issues SQL. Each takes an open connection from CommunityDB.session().

asyncpg port of general-bot's SQLAlchemy repositories: same classes, methods and return values.
Rows come back as attribute-access objects (row.total_xp), like the ORM objects did.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import asyncpg

from core.db import affected
from features.community.models import XpSource

# Writable columns of community.guild_settings (everything but the key and timestamps).
GUILD_SETTINGS_COLUMNS = frozenset(
    {
        "welcome_enabled", "welcome_channel_id", "welcome_text", "welcome_gif_url", "welcome_card_enabled",
        "goodbye_enabled", "goodbye_channel_id", "goodbye_text", "goodbye_gif_url", "goodbye_card_enabled",
        "goodbye_dm_enabled", "goodbye_dm_text",
        "leveling_enabled", "levelup_enabled", "levelup_channel_id", "levelup_text",
        "message_xp_min", "message_xp_max", "message_xp_cooldown",
        "voice_xp_amount", "voice_xp_interval", "voice_require_others", "voice_min_session",
        "voice_ignore_muted", "voice_ignore_deafened",
        "level_role_mode",
        "welcome_embed", "welcome_buttons", "welcome_use_embed",
        "goodbye_embed", "goodbye_buttons", "goodbye_use_embed",
        "levelup_embed", "levelup_buttons", "levelup_use_embed",
    }
)


def _row(record: asyncpg.Record | None) -> SimpleNamespace | None:
    return SimpleNamespace(**dict(record)) if record is not None else None


class GuildRepo:
    def __init__(self, session: asyncpg.Connection) -> None:
        self.session = session

    async def get(self, guild_id: int) -> SimpleNamespace | None:
        return _row(await self.session.fetchrow("SELECT * FROM community.guild_settings WHERE guild_id = $1", guild_id))

    async def get_or_create(self, guild_id: int) -> SimpleNamespace:
        # Insert-if-missing in one statement so concurrent callers can't collide.
        await self.session.execute(
            "INSERT INTO community.guild_settings (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING", guild_id
        )
        settings = await self.get(guild_id)
        assert settings is not None
        return settings

    async def update(self, guild_id: int, **fields: Any) -> SimpleNamespace:
        unknown = set(fields) - GUILD_SETTINGS_COLUMNS
        if unknown:
            raise ValueError(f"Unknown guild setting: {', '.join(sorted(unknown))}")
        await self.get_or_create(guild_id)
        if not fields:
            return await self.get(guild_id)
        assignments = ", ".join(f"{name} = ${i}" for i, name in enumerate(fields, start=2))
        return _row(
            await self.session.fetchrow(
                f"UPDATE community.guild_settings SET {assignments}, updated_at = NOW() WHERE guild_id = $1 RETURNING *",
                guild_id,
                *fields.values(),
            )
        )

    async def ensure_many(self, guild_ids: list[int]) -> None:
        if not guild_ids:
            return
        await self.session.execute(
            "INSERT INTO community.guild_settings (guild_id) SELECT unnest($1::bigint[]) ON CONFLICT (guild_id) DO NOTHING",
            guild_ids,
        )


# XP sources that have their own running total next to total_xp.
_SOURCE_COLUMNS = {XpSource.MESSAGE: "message_xp", XpSource.VOICE: "voice_xp"}


class MemberLevelRepo:
    def __init__(self, session: asyncpg.Connection) -> None:
        self.session = session

    async def add_xp(
        self, guild_id: int, user_id: int, amount: int, source: XpSource, *, name: str | None = None
    ) -> tuple[int, int]:
        """Add XP in one upsert. Returns (new total_xp, level as stored before this call).

        The upsert locks the member's row until the transaction commits, so concurrent awards
        for the same member run one after another and a level-up is only reported once.
        """
        column = _SOURCE_COLUMNS.get(source)
        source_set = f", {column} = m.{column} + EXCLUDED.{column}" if column else ""
        name_set = ", last_known_name = EXCLUDED.last_known_name" if name is not None else ""
        # The level column isn't touched here, so RETURNING gives the level from before this award.
        record = await self.session.fetchrow(
            f"""
            INSERT INTO community.member_levels AS m (guild_id, user_id, total_xp, message_xp, voice_xp, last_known_name)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                total_xp = m.total_xp + EXCLUDED.total_xp, updated_at = NOW(){source_set}{name_set}
            RETURNING total_xp, level
            """,
            guild_id,
            user_id,
            amount,
            amount if column == "message_xp" else 0,
            amount if column == "voice_xp" else 0,
            name[:100] if name is not None else None,
        )
        return record["total_xp"], record["level"]

    async def set_level(self, guild_id: int, user_id: int, level: int) -> None:
        await self.session.execute(
            "UPDATE community.member_levels SET level = $3 WHERE guild_id = $1 AND user_id = $2", guild_id, user_id, level
        )

    async def get(self, guild_id: int, user_id: int) -> SimpleNamespace | None:
        return _row(
            await self.session.fetchrow(
                "SELECT * FROM community.member_levels WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
            )
        )

    async def position(self, guild_id: int, total_xp: int) -> int:
        """1-based leaderboard position for an XP total; equal totals share a position."""
        higher = await self.session.fetchval(
            "SELECT COUNT(*) FROM community.member_levels WHERE guild_id = $1 AND total_xp > $2", guild_id, total_xp
        )
        return (higher or 0) + 1

    async def count(self, guild_id: int) -> int:
        return await self.session.fetchval("SELECT COUNT(*) FROM community.member_levels WHERE guild_id = $1", guild_id) or 0

    async def change_total(
        self, guild_id: int, user_id: int, *, set_to: int | None = None, delta: int = 0, name: str | None = None
    ) -> tuple[int, int, int]:
        """Set or adjust total_xp (never below 0). Returns (old_total, new_total, old_level).

        For admin changes only: it reads (locking the row) then writes, unlike add_xp's single upsert.
        """
        row = await self.session.fetchrow(
            "SELECT total_xp, level FROM community.member_levels WHERE guild_id = $1 AND user_id = $2 FOR UPDATE",
            guild_id,
            user_id,
        )
        old_total, old_level = (row["total_xp"], row["level"]) if row else (0, 0)
        new_total = max(0, set_to if set_to is not None else old_total + delta)
        if row is None:
            await self.session.execute(
                "INSERT INTO community.member_levels (guild_id, user_id, total_xp, last_known_name) VALUES ($1, $2, $3, $4)",
                guild_id,
                user_id,
                new_total,
                name[:100] if name is not None else None,
            )
        else:
            await self.session.execute(
                "UPDATE community.member_levels SET total_xp = $3, last_known_name = COALESCE($4, last_known_name), "
                "updated_at = NOW() WHERE guild_id = $1 AND user_id = $2",
                guild_id,
                user_id,
                new_total,
                name[:100] if name is not None else None,
            )
        return old_total, new_total, old_level

    async def delete(self, guild_id: int, user_id: int) -> SimpleNamespace | None:
        return _row(
            await self.session.fetchrow(
                "DELETE FROM community.member_levels WHERE guild_id = $1 AND user_id = $2 RETURNING *", guild_id, user_id
            )
        )

    async def delete_guild(self, guild_id: int) -> int:
        return affected(await self.session.execute("DELETE FROM community.member_levels WHERE guild_id = $1", guild_id))

    async def page(self, guild_id: int, offset: int, limit: int) -> list[tuple[int, SimpleNamespace]]:
        """(position, row) ordered by XP; equal XP shares a position (RANK())."""
        records = await self.session.fetch(
            """
            SELECT *, RANK() OVER (ORDER BY total_xp DESC) AS position
            FROM community.member_levels WHERE guild_id = $1
            ORDER BY total_xp DESC, user_id OFFSET $2 LIMIT $3
            """,
            guild_id,
            offset,
            limit,
        )
        result = []
        for record in records:
            values = dict(record)
            position = values.pop("position")
            result.append((position, SimpleNamespace(**values)))
        return result


class LevelRoleRepo:
    def __init__(self, session: asyncpg.Connection) -> None:
        self.session = session

    async def list(self, guild_id: int) -> list[SimpleNamespace]:
        records = await self.session.fetch(
            "SELECT * FROM community.level_roles WHERE guild_id = $1 ORDER BY level", guild_id
        )
        return [_row(r) for r in records]

    async def set(self, guild_id: int, level: int, role_id: int | None) -> None:
        """Add a milestone, or point an existing one at a different role (None = create on first use)."""
        await self.session.execute(
            "INSERT INTO community.level_roles (guild_id, level, role_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, level) DO UPDATE SET role_id = EXCLUDED.role_id",
            guild_id,
            level,
            role_id,
        )

    async def remove(self, guild_id: int, level: int) -> bool:
        status = await self.session.execute(
            "DELETE FROM community.level_roles WHERE guild_id = $1 AND level = $2", guild_id, level
        )
        return affected(status) > 0

    async def members_at_or_above(self, guild_id: int, level: int) -> list[tuple[int, int]]:
        """(user_id, level) for members whose level is at least the given one."""
        records = await self.session.fetch(
            "SELECT user_id, level FROM community.member_levels WHERE guild_id = $1 AND level >= $2 ORDER BY total_xp DESC",
            guild_id,
            level,
        )
        return [(r["user_id"], r["level"]) for r in records]


class XpLogRepo:
    def __init__(self, session: asyncpg.Connection) -> None:
        self.session = session

    async def add(self, guild_id: int, user_id: int, source: XpSource, amount: int) -> None:
        await self.session.execute(
            "INSERT INTO community.xp_log (guild_id, user_id, source, amount) VALUES ($1, $2, $3, $4)",
            guild_id,
            user_id,
            source.value,
            amount,
        )

    async def prune(self, older_than: datetime) -> int:
        return affected(await self.session.execute("DELETE FROM community.xp_log WHERE created_at < $1", older_than))


class MediaRepo:
    def __init__(self, session: asyncpg.Connection) -> None:
        self.session = session

    async def get(self, guild_id: int, kind: str) -> bytes | None:
        return await self.session.fetchval(
            "SELECT data FROM community.guild_media WHERE guild_id = $1 AND kind = $2", guild_id, kind
        )

    async def exists(self, guild_id: int, kind: str) -> bool:
        return bool(
            await self.session.fetchval(
                "SELECT EXISTS (SELECT 1 FROM community.guild_media WHERE guild_id = $1 AND kind = $2)", guild_id, kind
            )
        )

    async def put(self, guild_id: int, kind: str, data: bytes) -> None:
        await self.session.execute(
            "INSERT INTO community.guild_media (guild_id, kind, data, updated_at) VALUES ($1, $2, $3, NOW()) "
            "ON CONFLICT (guild_id, kind) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
            guild_id,
            kind,
            data,
        )

    async def delete(self, guild_id: int, kind: str) -> None:
        await self.session.execute("DELETE FROM community.guild_media WHERE guild_id = $1 AND kind = $2", guild_id, kind)
