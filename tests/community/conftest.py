"""The community tests' `db`: the shared migrated test database, wrapped as a CommunityDB."""

from __future__ import annotations

import pytest

from features.community.db import CommunityDB


@pytest.fixture
async def db(core_db):
    return CommunityDB(core_db.pool)
