"""Nickname tables (schema `nickname`) on the bot's shared pool.

Async port of nickname-bot's sqlite3 Database: same method names and return types, so the
services and cogs only gained `await`. Times are TIMESTAMPTZ in Postgres but cross this
boundary as epoch seconds (float), which is what the job code works in.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import asyncpg

MODES = ("off", "new", "existing", "both")


@dataclass
class GuildSettings:
    guild_id: int
    mode: str = "off"
    template: str | None = None
    include_bots: bool = False
    excluded_roles: set[int] = field(default_factory=set)
    excluded_members: set[int] = field(default_factory=set)

    @property
    def renames_new_members(self) -> bool:
        return self.mode in ("new", "both")

    @property
    def allows_bulk(self) -> bool:
        return self.mode in ("existing", "both")


@dataclass
class NicknameRecord:
    original_nick: str | None
    applied_nick: str | None


JOB_COLUMNS = (
    "id, guild_id, kind, started_by, status, total, processed, changed, skipped, failed, "
    "EXTRACT(EPOCH FROM started_at)::float8 AS started_at, EXTRACT(EPOCH FROM finished_at)::float8 AS finished_at"
)


class NicknameDB:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def mark_interrupted_jobs(self, quiet_seconds: int = 120) -> int:
        """Jobs still marked running but silent for a while were cut off by a restart or crash.

        A running job saves progress (its heartbeat) every few members, so a job from another instance
        that's still alive during a deploy isn't caught by this.
        """
        status = await self.pool.execute(
            "UPDATE nickname.jobs SET status = 'interrupted', finished_at = NOW() "
            "WHERE status = 'running' AND heartbeat_at < NOW() - make_interval(secs => $1)",
            quiet_seconds,
        )
        return int(status.split()[-1])

    async def ping(self) -> bool:
        try:
            await self.pool.fetchval("SELECT 1")
            return True
        except (asyncpg.PostgresError, OSError):
            return False

    # --- settings -----------------------------------------------------------

    async def get_settings(self, guild_id: int) -> GuildSettings:
        row = await self.pool.fetchrow("SELECT * FROM nickname.guild_settings WHERE guild_id = $1", guild_id)
        if row is None:
            return GuildSettings(guild_id=guild_id)
        return GuildSettings(
            guild_id=guild_id,
            mode=row["mode"],
            template=row["template"],
            include_bots=row["include_bots"],
            excluded_roles=set(row["excluded_roles"]),
            excluded_members=set(row["excluded_members"]),
        )

    async def save_settings(self, settings: GuildSettings) -> None:
        await self.pool.execute(
            """
            INSERT INTO nickname.guild_settings
                (guild_id, mode, template, include_bots, excluded_roles, excluded_members)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (guild_id) DO UPDATE SET
                mode = EXCLUDED.mode,
                template = EXCLUDED.template,
                include_bots = EXCLUDED.include_bots,
                excluded_roles = EXCLUDED.excluded_roles,
                excluded_members = EXCLUDED.excluded_members
            """,
            settings.guild_id,
            settings.mode,
            settings.template,
            settings.include_bots,
            sorted(settings.excluded_roles),
            sorted(settings.excluded_members),
        )

    # --- original nicknames ---------------------------------------------------

    async def get_record(self, guild_id: int, user_id: int) -> NicknameRecord | None:
        row = await self.pool.fetchrow(
            "SELECT original_nick, applied_nick FROM nickname.original_nicknames WHERE guild_id = $1 AND user_id = $2",
            guild_id,
            user_id,
        )
        return NicknameRecord(row["original_nick"], row["applied_nick"]) if row else None

    async def get_all_records(self, guild_id: int) -> dict[int, NicknameRecord]:
        rows = await self.pool.fetch(
            "SELECT user_id, original_nick, applied_nick FROM nickname.original_nicknames WHERE guild_id = $1",
            guild_id,
        )
        return {r["user_id"]: NicknameRecord(r["original_nick"], r["applied_nick"]) for r in rows}

    async def save_record(self, guild_id: int, user_id: int, original_nick: str | None, applied_nick: str | None) -> None:
        await self.pool.execute(
            """
            INSERT INTO nickname.original_nicknames (guild_id, user_id, original_nick, applied_nick, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                original_nick = EXCLUDED.original_nick,
                applied_nick = EXCLUDED.applied_nick,
                updated_at = EXCLUDED.updated_at
            """,
            guild_id,
            user_id,
            original_nick,
            applied_nick,
        )

    async def delete_record(self, guild_id: int, user_id: int) -> None:
        await self.pool.execute(
            "DELETE FROM nickname.original_nicknames WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
        )

    async def delete_guild_data(self, guild_id: int) -> None:
        """Remove everything stored for a server (used when the bot leaves it)."""
        async with self.pool.acquire() as conn, conn.transaction():
            for table in ("guild_settings", "original_nicknames", "jobs", "change_log"):
                await conn.execute(f"DELETE FROM nickname.{table} WHERE guild_id = $1", guild_id)

    # --- jobs and change log --------------------------------------------------

    async def create_job(self, guild_id: int, kind: str, started_by: int, total: int, started_at: float) -> int:
        return await self.pool.fetchval(
            "INSERT INTO nickname.jobs (guild_id, kind, started_by, status, total, started_at) "
            "VALUES ($1, $2, $3, 'running', $4, to_timestamp($5)) RETURNING id",
            guild_id,
            kind,
            started_by,
            total,
            started_at,
        )

    async def update_job(
        self,
        job_id: int,
        *,
        status: str,
        processed: int,
        changed: int,
        skipped: Counter,
        failed: Counter,
        finished_at: float | None = None,
    ) -> None:
        await self.pool.execute(
            """
            UPDATE nickname.jobs
            SET status = $2, processed = $3, changed = $4, skipped = $5, failed = $6, finished_at = to_timestamp($7), heartbeat_at = NOW()
            WHERE id = $1
            """,
            job_id,
            status,
            processed,
            changed,
            dict(skipped),
            dict(failed),
            finished_at,
        )

    async def last_job(self, guild_id: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            f"SELECT {JOB_COLUMNS} FROM nickname.jobs WHERE guild_id = $1 ORDER BY id DESC LIMIT 1", guild_id
        )

    async def log_change(
        self,
        guild_id: int,
        user_id: int,
        source: str,
        old_nick: str | None,
        new_nick: str | None,
        result: str,
        reason: str | None = None,
        job_id: int | None = None,
    ) -> None:
        await self.pool.execute(
            """
            INSERT INTO nickname.change_log (guild_id, user_id, job_id, source, old_nick, new_nick, result, reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            guild_id,
            user_id,
            job_id,
            source,
            old_nick,
            new_nick,
            result,
            reason,
        )
