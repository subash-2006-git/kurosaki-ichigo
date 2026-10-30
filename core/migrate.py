"""Apply migrations/NNNN_name.sql files in order, once each.

Everything pending runs in one transaction behind a transaction-scoped advisory lock, so two
instances starting together (Render's zero-downtime deploy) can't both apply the same file. A
transaction-scoped lock is used because session locks don't survive Neon's PgBouncer pooler.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from core.config import ROOT_DIR
from core.db import Database

log = logging.getLogger(__name__)

MIGRATIONS_DIR = ROOT_DIR / "migrations"
FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
LOCK_KEY = 0x6B75726F  # arbitrary, constant across deploys

BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.schema_migrations (
    version     TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = FILENAME.match(path.name)
        if not match:
            raise ValueError(f"Bad migration filename {path.name!r}; expected NNNN_name.sql")
        migrations.append(Migration(version=path.stem, path=path))
    numbers = [m.version[:4] for m in migrations]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"Duplicate migration numbers in {directory}")
    return migrations


async def run(db: Database, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied."""
    migrations = discover(directory)
    applied_now: list[str] = []
    async with db.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
        await conn.execute(BOOTSTRAP)
        applied = {r["version"]: r["checksum"] for r in await conn.fetch("SELECT version, checksum FROM core.schema_migrations")}
        for migration in migrations:
            if migration.version in applied:
                if applied[migration.version] != migration.checksum:
                    log.warning("Migration %s changed after it was applied; edit a new file instead", migration.version)
                continue
            log.info("Applying migration %s", migration.version)
            await conn.execute(migration.sql)
            await conn.execute(
                "INSERT INTO core.schema_migrations (version, checksum) VALUES ($1, $2)",
                migration.version,
                migration.checksum,
            )
            applied_now.append(migration.version)
    log.info("Migrations up to date (%d applied now, %d total)", len(applied_now), len(migrations))
    return applied_now
