"""Shared fixtures: an embedded Postgres (pgserver) and a fresh, fully migrated database per test."""

from __future__ import annotations

import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from core import migrate
from core.db import Database


@pytest.fixture(scope="session")
def postgres_admin_url(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    yield server.get_uri()


def with_database(url: str, name: str) -> str:
    """The same server URL pointing at another database. Keeps the query: on Linux pgserver
    connects through a Unix socket given as ?host=/tmp/..., which contains slashes."""
    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


@pytest.fixture
async def core_db(postgres_admin_url):
    """A core Database on a brand-new database with every migration in migrations/ applied."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(postgres_admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    database = Database(with_database(postgres_admin_url, name), ssl="disable")
    await database.connect()
    await migrate.run(database)
    yield database
    await database.close()
    admin = await asyncpg.connect(postgres_admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
