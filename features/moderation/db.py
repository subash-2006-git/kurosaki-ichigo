"""Moderation tables (schema `moderation`) on the bot's shared pool. Ported from mod-bot."""
import logging
from datetime import datetime

import asyncpg

from core.db import affected as _count

log = logging.getLogger(__name__)

# Columns of guild_config that update_guild_config() is allowed to write.
GUILD_CONFIG_COLUMNS = {
    "modlog_channel_id",
    "report_channel_id",
    "alert_channel_id",
    "appeal_url",
    "dm_on_action",
    "automod",
    "raid",
    "raidmode_active",
    "locked_channels",
}


class ModerationDB:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
        # Earliest pending scheduled action, kept in memory so the expiry loop only
        # queries when something is due and Neon can scale to zero while idle.
        self.next_scheduled: datetime | None = None

    # ---- guild config -------------------------------------------------

    async def get_guild_config(self, guild_id: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow("SELECT * FROM moderation.guild_config WHERE guild_id = $1", guild_id)

    async def update_guild_config(self, guild_id: int, **fields) -> None:
        unknown = set(fields) - GUILD_CONFIG_COLUMNS
        if unknown:
            raise ValueError(f"Unknown guild_config columns: {unknown}")
        cols = list(fields)
        placeholders = ", ".join(f"${i + 2}" for i in range(len(cols)))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        await self.pool.execute(
            f"INSERT INTO moderation.guild_config (guild_id, {', '.join(cols)}) VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}",
            guild_id,
            *fields.values(),
        )

    # ---- cases --------------------------------------------------------

    async def create_case(
        self,
        guild_id: int,
        user_id: int,
        mod_id: int,
        action: str,
        reason: str | None,
        duration_seconds: int | None = None,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn, conn.transaction():
            case_num = await conn.fetchval(
                "INSERT INTO moderation.guild_config (guild_id, case_counter) VALUES ($1, 1) "
                "ON CONFLICT (guild_id) DO UPDATE SET case_counter = guild_config.case_counter + 1 "
                "RETURNING case_counter",
                guild_id,
            )
            return await conn.fetchrow(
                "INSERT INTO moderation.cases (guild_id, case_num, user_id, mod_id, action, reason, duration_seconds) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING *",
                guild_id, case_num, user_id, mod_id, action, reason, duration_seconds,
            )

    async def get_case(self, guild_id: int, case_num: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM moderation.cases WHERE guild_id = $1 AND case_num = $2", guild_id, case_num
        )

    async def get_user_cases(self, guild_id: int, user_id: int, limit: int = 25) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM moderation.cases WHERE guild_id = $1 AND user_id = $2 ORDER BY case_num DESC LIMIT $3",
            guild_id, user_id, limit,
        )

    async def update_case_reason(self, guild_id: int, case_num: int, reason: str) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "UPDATE moderation.cases SET reason = $3 WHERE guild_id = $1 AND case_num = $2 RETURNING *",
            guild_id, case_num, reason,
        )

    async def get_active_warns(self, guild_id: int, user_id: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM moderation.cases WHERE guild_id = $1 AND user_id = $2 AND action = 'warn' AND active "
            "ORDER BY case_num",
            guild_id, user_id,
        )

    async def count_active_warns(self, guild_id: int, user_id: int) -> int:
        return await self.pool.fetchval(
            "SELECT COUNT(*) FROM moderation.cases WHERE guild_id = $1 AND user_id = $2 AND action = 'warn' AND active",
            guild_id, user_id,
        )

    async def deactivate_warn(self, guild_id: int, case_num: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "UPDATE moderation.cases SET active = FALSE WHERE guild_id = $1 AND case_num = $2 AND action = 'warn' "
            "AND active RETURNING *",
            guild_id, case_num,
        )

    async def clear_warns(self, guild_id: int, user_id: int) -> int:
        status = await self.pool.execute(
            "UPDATE moderation.cases SET active = FALSE WHERE guild_id = $1 AND user_id = $2 AND action = 'warn' AND active",
            guild_id, user_id,
        )
        return _count(status)

    # ---- warn thresholds ----------------------------------------------

    async def get_threshold(self, guild_id: int, warn_count: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM moderation.warn_thresholds WHERE guild_id = $1 AND warn_count = $2", guild_id, warn_count
        )

    async def list_thresholds(self, guild_id: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM moderation.warn_thresholds WHERE guild_id = $1 ORDER BY warn_count", guild_id
        )

    async def set_threshold(self, guild_id: int, warn_count: int, action: str, duration_seconds: int | None) -> None:
        await self.pool.execute(
            "INSERT INTO moderation.warn_thresholds (guild_id, warn_count, action, duration_seconds) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, warn_count) DO UPDATE SET action = EXCLUDED.action, "
            "duration_seconds = EXCLUDED.duration_seconds",
            guild_id, warn_count, action, duration_seconds,
        )

    async def delete_threshold(self, guild_id: int, warn_count: int) -> bool:
        status = await self.pool.execute(
            "DELETE FROM moderation.warn_thresholds WHERE guild_id = $1 AND warn_count = $2", guild_id, warn_count
        )
        return _count(status) > 0

    # ---- scheduled actions --------------------------------------------

    async def add_scheduled(
        self, guild_id: int, user_id: int, type_: str, expires_at: datetime, role_id: int | None = None
    ) -> None:
        await self.pool.execute(
            "INSERT INTO moderation.scheduled_actions (guild_id, user_id, type, role_id, expires_at) VALUES ($1, $2, $3, $4, $5)",
            guild_id, user_id, type_, role_id, expires_at,
        )
        if self.next_scheduled is None or expires_at < self.next_scheduled:
            self.next_scheduled = expires_at

    async def refresh_next_scheduled(self) -> None:
        self.next_scheduled = await self.pool.fetchval("SELECT MIN(expires_at) FROM moderation.scheduled_actions")

    async def clear_scheduled(self, guild_id: int, user_id: int, type_: str, role_id: int | None = None) -> None:
        if role_id is None:
            await self.pool.execute(
                "DELETE FROM moderation.scheduled_actions WHERE guild_id = $1 AND user_id = $2 AND type = $3",
                guild_id, user_id, type_,
            )
        else:
            await self.pool.execute(
                "DELETE FROM moderation.scheduled_actions WHERE guild_id = $1 AND user_id = $2 AND type = $3 AND role_id = $4",
                guild_id, user_id, type_, role_id,
            )

    async def get_due_scheduled(self) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM moderation.scheduled_actions WHERE expires_at <= NOW() ORDER BY expires_at LIMIT 50"
        )

    async def delete_scheduled(self, action_id: int) -> None:
        await self.pool.execute("DELETE FROM moderation.scheduled_actions WHERE id = $1", action_id)

    # ---- notes --------------------------------------------------------

    async def add_note(self, guild_id: int, user_id: int, author_id: int, content: str) -> asyncpg.Record:
        return await self.pool.fetchrow(
            "INSERT INTO moderation.notes (guild_id, user_id, author_id, content) VALUES ($1, $2, $3, $4) RETURNING *",
            guild_id, user_id, author_id, content,
        )

    async def get_notes(self, guild_id: int, user_id: int) -> list[asyncpg.Record]:
        return await self.pool.fetch(
            "SELECT * FROM moderation.notes WHERE guild_id = $1 AND user_id = $2 ORDER BY id DESC LIMIT 25",
            guild_id, user_id,
        )

    async def delete_note(self, guild_id: int, note_id: int) -> bool:
        status = await self.pool.execute("DELETE FROM moderation.notes WHERE guild_id = $1 AND id = $2", guild_id, note_id)
        return _count(status) > 0

    # ---- banned words / link whitelist ---------------------------------

    async def get_banned_words(self, guild_id: int) -> list[str]:
        rows = await self.pool.fetch("SELECT word FROM moderation.banned_words WHERE guild_id = $1 ORDER BY word", guild_id)
        return [r["word"] for r in rows]

    async def add_banned_word(self, guild_id: int, word: str) -> bool:
        status = await self.pool.execute(
            "INSERT INTO moderation.banned_words (guild_id, word) VALUES ($1, $2) ON CONFLICT DO NOTHING", guild_id, word
        )
        return _count(status) > 0

    async def remove_banned_word(self, guild_id: int, word: str) -> bool:
        status = await self.pool.execute(
            "DELETE FROM moderation.banned_words WHERE guild_id = $1 AND word = $2", guild_id, word
        )
        return _count(status) > 0

    async def get_whitelist(self, guild_id: int) -> list[str]:
        rows = await self.pool.fetch("SELECT domain FROM moderation.link_whitelist WHERE guild_id = $1 ORDER BY domain", guild_id)
        return [r["domain"] for r in rows]

    async def add_whitelist(self, guild_id: int, domain: str) -> bool:
        status = await self.pool.execute(
            "INSERT INTO moderation.link_whitelist (guild_id, domain) VALUES ($1, $2) ON CONFLICT DO NOTHING", guild_id, domain
        )
        return _count(status) > 0

    async def remove_whitelist(self, guild_id: int, domain: str) -> bool:
        status = await self.pool.execute(
            "DELETE FROM moderation.link_whitelist WHERE guild_id = $1 AND domain = $2", guild_id, domain
        )
        return _count(status) > 0

    # ---- reports ------------------------------------------------------

    async def create_report(
        self, guild_id: int, reporter_id: int, target_id: int, reason: str, message_link: str | None
    ) -> asyncpg.Record:
        return await self.pool.fetchrow(
            "INSERT INTO moderation.reports (guild_id, reporter_id, target_id, reason, message_link) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING *",
            guild_id, reporter_id, target_id, reason, message_link,
        )

    async def get_report(self, guild_id: int, report_id: int) -> asyncpg.Record | None:
        return await self.pool.fetchrow(
            "SELECT * FROM moderation.reports WHERE guild_id = $1 AND id = $2", guild_id, report_id
        )

    async def close_report(self, report_id: int, status: str, handled_by: int) -> bool:
        """Mark an open report handled. Returns False if someone else already handled it."""
        status_str = await self.pool.execute(
            "UPDATE moderation.reports SET status = $2, handled_by = $3 WHERE id = $1 AND status = 'open'",
            report_id, status, handled_by,
        )
        return _count(status_str) > 0

    async def reopen_report(self, report_id: int) -> None:
        await self.pool.execute(
            "UPDATE moderation.reports SET status = 'open', handled_by = NULL WHERE id = $1", report_id
        )
