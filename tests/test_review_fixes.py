"""Nickname jobs from another live instance aren't mislabelled; OWNER_IDS parsing."""

import importlib
from collections import Counter

import core.config as config
from features.nickname.db import NicknameDB


async def test_only_silent_running_jobs_are_marked_interrupted(core_db):
    db = NicknameDB(core_db.pool)
    live = await db.create_job(1, "apply", 9, 10, 0.0)
    silent = await db.create_job(1, "apply", 9, 10, 0.0)
    await core_db.pool.execute("UPDATE nickname.jobs SET heartbeat_at = NOW() - interval '10 minutes' WHERE id = $1", silent)
    assert await db.mark_interrupted_jobs() == 1
    statuses = dict(await core_db.pool.fetch("SELECT id, status FROM nickname.jobs"))
    assert statuses == {live: "running", silent: "interrupted"}


async def test_saving_progress_refreshes_the_heartbeat(core_db):
    db = NicknameDB(core_db.pool)
    job = await db.create_job(1, "apply", 9, 10, 0.0)
    await core_db.pool.execute("UPDATE nickname.jobs SET heartbeat_at = NOW() - interval '10 minutes' WHERE id = $1", job)
    await db.update_job(job, status="running", processed=5, changed=5, skipped=Counter(), failed=Counter())
    assert await db.mark_interrupted_jobs() == 0


def test_owner_ids(monkeypatch):
    monkeypatch.setenv("OWNER_IDS", " 123, 456 ,x,")
    assert importlib.reload(config).OWNER_IDS == frozenset({123, 456})
    monkeypatch.setenv("OWNER_IDS", "")
    assert importlib.reload(config).OWNER_IDS == frozenset()
