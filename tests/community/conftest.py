"""Database fixture for the community tests: a fresh embedded Postgres database per test (pgserver),
with every migration in migrations/ applied, so the real schema and SQL are exercised."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from core import migrate
from core.db import Database
from features.community.db import CommunityDB


@pytest.fixture(scope="session")
def postgres_admin_url(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    yield server.get_uri()


@pytest.fixture
async def db(postgres_admin_url):
    name = f"test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(postgres_admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    core_db = Database(f"{postgres_admin_url.rsplit('/', 1)[0]}/{name}", ssl="disable")
    await core_db.connect()
    await migrate.run(core_db)
    yield CommunityDB(core_db.pool)
    await core_db.close()
    admin = await asyncpg.connect(postgres_admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()
