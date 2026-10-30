"""Transactions for the community feature on the bot's shared pool.

general-bot's services open `async with db.session() as session:` and hand the session to the
repositories. That shape is kept: a "session" is now an asyncpg connection inside a transaction
that commits on success and rolls back on error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg


class CommunityDB:
    backend = "postgresql"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @asynccontextmanager
    async def session(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as conn, conn.transaction():
            yield conn
