"""The one asyncpg pool every feature shares.

Tuned for Neon: the pool may shrink to zero and closes idle connections after a minute so the
compute can suspend, and the statement cache is off so the `-pooler` (PgBouncer) endpoint works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

log = logging.getLogger(__name__)


def clean_dsn(dsn: str) -> str:
    """Drop libpq-only query params (Neon adds them) that asyncpg would reject, and normalise the scheme."""
    parts = urlsplit(dsn)
    scheme = "postgresql" if parts.scheme in {"postgres", "postgresql+asyncpg"} else parts.scheme
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"sslmode", "channel_binding"}]
    return urlunsplit(parts._replace(scheme=scheme, query=urlencode(query)))


def affected(status: str) -> int:
    """Turn an asyncpg status string like 'DELETE 3' into 3."""
    try:
        return int(status.split()[-1])
    except (ValueError, IndexError):
        return 0


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


class Database:
    def __init__(self, dsn: str, ssl: str = "require") -> None:
        self.dsn = clean_dsn(dsn)
        self.ssl = None if ssl == "disable" else ssl
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database.connect() has not been called")
        return self._pool

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self.dsn,
            ssl=self.ssl,
            min_size=0,
            max_size=10,
            max_inactive_connection_lifetime=60,
            statement_cache_size=0,
            command_timeout=30,
            init=_init_connection,
        )
        log.info("Database pool ready")

    async def close(self, timeout: float = 10.0) -> None:
        """Let queries in progress finish (up to `timeout`), then close."""
        if self._pool is not None:
            try:
                await asyncio.wait_for(self._pool.close(), timeout)
            except asyncio.TimeoutError:
                self._pool.terminate()
            self._pool = None

    async def ping(self, timeout: float = 15.0) -> float:
        """Seconds for one SELECT 1 round trip. Generous timeout: a suspended Neon compute takes a moment to wake."""
        start = time.perf_counter()
        await asyncio.wait_for(self.pool.fetchval("SELECT 1"), timeout)
        return time.perf_counter() - start
