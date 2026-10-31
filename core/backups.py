"""Restore one server's settings from a daily backup.

The backups are the encrypted pg_dump files that .github/workflows/backup.yml keeps as workflow
artifacts. The bot downloads one through the GitHub API (BACKUP_REPO + GITHUB_TOKEN), decrypts it
(BACKUP_PASSPHRASE, the same openssl format the workflow writes), reads the COPY data for the
settings tables, and replaces just this server's rows in one transaction.

Features say which of their tables are settings by adding a RestoreTarget to bot.restore_targets,
with a `reset` that drops their cached copy; core never imports features. Counters that only go up
(case numbers…) keep the higher value, and live state (raid mode, today's birthdays run) stays as it is.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import logging
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
import asyncpg
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core import config
from core.db import affected

log = logging.getLogger(__name__)

API = "https://api.github.com"
PBKDF2_ITERATIONS = 200_000  # matches `openssl enc -pbkdf2 -iter 200000` in backup.yml
MAX_LISTED = 25


@dataclass(frozen=True)
class RestoreTarget:
    table: str                                   # schema.table, with a guild_id column
    label: str                                   # what it is, for the preview
    keep: tuple[str, ...] = ()                   # columns left as they are now (live state)
    keep_max: tuple[str, ...] = ()               # counters: keep the higher of now and the backup
    reset: Callable[[int], Awaitable[None] | None] | None = None  # drop cached copies for a guild


@dataclass(frozen=True)
class Backup:
    id: int
    name: str
    created_at: datetime
    size: int


@dataclass
class TableData:
    columns: list[str]
    lines: list[str] = field(default_factory=list)  # COPY text rows

    def for_guild(self, guild_id: int) -> list[str]:
        index = self.columns.index("guild_id")
        wanted = str(guild_id)
        return [line for line in self.lines if line.split("\t")[index] == wanted]


def configured() -> bool:
    return bool(config.BACKUP_REPO and config.GITHUB_TOKEN and config.BACKUP_PASSPHRASE)


# ---- decrypting and reading a dump (pure, tested) --------------------------------------------


def decrypt(blob: bytes, passphrase: str) -> bytes:
    """`openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt` → plaintext. ValueError on a wrong passphrase."""
    if not blob.startswith(b"Salted__") or len(blob) < 32:
        raise ValueError("That file isn't an encrypted backup.")
    salt, body = blob[8:16], blob[16:]
    key_iv = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, PBKDF2_ITERATIONS, dklen=48)
    decryptor = Cipher(algorithms.AES(key_iv[:32]), modes.CBC(key_iv[32:])).decryptor()
    padded = decryptor.update(body) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        raise ValueError("The backup couldn't be decrypted. Is BACKUP_PASSPHRASE the one the backup was made with?") from None


def parse_dump(sql: str, tables: set[str]) -> dict[str, TableData]:
    """The COPY data for the wanted tables in a plain pg_dump."""
    found: dict[str, TableData] = {}
    current: TableData | None = None
    for line in sql.split("\n"):
        if current is not None:
            if line == "\\.":
                current = None
            else:
                current.lines.append(line)
            continue
        if line.startswith("COPY ") and line.endswith(" FROM stdin;"):
            name, _, rest = line[5:].partition(" (")
            name = name.replace('"', "")
            if name in tables:
                columns = [c.strip().strip('"') for c in rest[: rest.rindex(")")].split(",")]
                current = found[name] = TableData(columns)
    return found


def read_backup(encrypted: bytes, passphrase: str, tables: set[str]) -> dict[str, TableData]:
    return parse_dump(gzip.decompress(decrypt(encrypted, passphrase)).decode("utf-8"), tables)


# ---- GitHub ------------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


async def list_backups() -> list[Backup]:
    url = f"{API}/repos/{config.BACKUP_REPO}/actions/artifacts?per_page=100"
    async with aiohttp.ClientSession(headers=_headers()) as session, session.get(url) as resp:
        if resp.status != 200:
            raise RuntimeError(f"GitHub answered {resp.status} listing backups. Check BACKUP_REPO and GITHUB_TOKEN.")
        data = await resp.json()
    backups = [
        Backup(a["id"], a["name"], datetime.fromisoformat(a["created_at"].replace("Z", "+00:00")), a["size_in_bytes"])
        for a in data.get("artifacts", []) if a["name"].startswith("backup-") and not a["expired"]
    ]
    return sorted(backups, key=lambda b: b.created_at, reverse=True)[:MAX_LISTED]


async def download(backup: Backup) -> bytes:
    """The encrypted file inside the artifact's zip."""
    url = f"{API}/repos/{config.BACKUP_REPO}/actions/artifacts/{backup.id}/zip"
    async with aiohttp.ClientSession() as session:
        # GitHub redirects to a storage URL; follow it without our token.
        async with session.get(url, headers=_headers(), allow_redirects=False) as resp:
            if resp.status not in (301, 302, 307):
                raise RuntimeError(f"GitHub answered {resp.status} downloading the backup.")
            location = resp.headers["Location"]
        async with session.get(location) as resp:
            resp.raise_for_status()
            archive = await resp.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return zf.read(zf.namelist()[0])


# ---- restoring ---------------------------------------------------------------------------


async def current_counts(pool: asyncpg.Pool, targets: list[RestoreTarget], guild_id: int) -> dict[str, int]:
    async with pool.acquire() as conn:
        return {t.table: await conn.fetchval(f"SELECT COUNT(*) FROM {t.table} WHERE guild_id = $1", guild_id)
                for t in targets}


async def restore(pool: asyncpg.Pool, targets: list[RestoreTarget], data: dict[str, TableData], guild_id: int) -> dict[str, int]:
    """Replace this server's rows in every target table with the backup's, in one transaction.
    Returns rows restored per table. Tables missing from the backup are left alone."""
    restored: dict[str, int] = {}
    async with pool.acquire() as conn, conn.transaction():
        for target in targets:
            table = data.get(target.table)
            if table is None:
                continue
            columns = [r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_schema || '.' || table_name = $1 "
                "ORDER BY ordinal_position", target.table)]
            if unknown := set(table.columns) - set(columns):
                raise ValueError(f"{target.table} in the backup has columns this bot doesn't: {', '.join(sorted(unknown))}")
            kept = await conn.fetchrow(
                f"SELECT {', '.join(target.keep + target.keep_max)} FROM {target.table} WHERE guild_id = $1", guild_id,
            ) if target.keep or target.keep_max else None
            rows = table.for_guild(guild_id)
            await conn.execute(f"CREATE TEMP TABLE restore_rows (LIKE {target.table} INCLUDING DEFAULTS) ON COMMIT DROP")
            if rows:
                await conn.copy_to_table("restore_rows", source=io.BytesIO(("\n".join(rows) + "\n").encode()),
                                         columns=table.columns, format="text")
            await conn.execute(f"DELETE FROM {target.table} WHERE guild_id = $1", guild_id)
            cols = ", ".join(columns)
            await conn.execute(f"INSERT INTO {target.table} ({cols}) OVERRIDING SYSTEM VALUE SELECT {cols} FROM restore_rows")
            if kept is not None:
                sets = [f"{c} = ${i}" for i, c in enumerate(target.keep, start=2)]
                sets += [f"{c} = GREATEST({c}, ${i})" for i, c in enumerate(target.keep_max, start=2 + len(target.keep))]
                status = await conn.execute(f"UPDATE {target.table} SET {', '.join(sets)} WHERE guild_id = $1",
                                            guild_id, *kept.values())
                if affected(status) == 0:  # no row in the backup: keep the live state and counters anyway
                    names = target.keep + target.keep_max
                    await conn.execute(
                        f"INSERT INTO {target.table} (guild_id, {', '.join(names)}) "
                        f"VALUES ($1, {', '.join(f'${i}' for i in range(2, len(names) + 2))})",
                        guild_id, *kept.values(),
                    )
            await conn.execute("DROP TABLE restore_rows")
            restored[target.table] = len(rows)
    for target in targets:
        if target.reset is not None and target.table in restored:
            result = target.reset(guild_id)
            if result is not None:
                await result
    log.info("Restored settings for guild %s: %s", guild_id, restored)
    return restored
