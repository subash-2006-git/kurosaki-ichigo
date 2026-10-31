"""Invite tracking: telling which invite was used, sharing one detection per join, storage and counts."""

import asyncio
from types import SimpleNamespace

import pytest

from features.invites.cog import top_inviters
from features.invites.db import InvitesDB
from features.invites.tracker import InviteTracker, used_invite


def invite(code, uses, inviter_id=None):
    inviter = SimpleNamespace(id=inviter_id, mention=f"<@{inviter_id}>") if inviter_id else None
    return SimpleNamespace(code=code, uses=uses, inviter=inviter, guild=SimpleNamespace(id=1))


def test_used_invite():
    before = {"aaa": 3, "bbb": 1}
    result = used_invite(before, [invite("aaa", 4, 7), invite("bbb", 1, 8)], None)
    assert (result.text, result.code, result.inviter_id) == ("`aaa` by <@7>", "aaa", 7)
    assert used_invite(before, [invite("aaa", 4), invite("bbb", 2)], None).text.startswith("Ambiguous")
    assert "vanity URL `island`" in used_invite(before, [invite("aaa", 3)], "island").text
    assert used_invite(None, [], None).text.startswith("Unknown *(invite list was still loading")
    assert used_invite({}, [invite("new", 1, 9)], None).inviter_id == 9  # created after the cache was loaded


async def test_one_detection_shared_per_join():
    calls = []

    async def invites():
        calls.append(1)
        await asyncio.sleep(0)
        return [invite("aaa", 1, 7)]

    guild = SimpleNamespace(id=1, me=SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=True)),
                            invites=invites, vanity_url_code=None)
    tracker = InviteTracker()
    tracker._uses[1] = {"aaa": 0}
    member = SimpleNamespace(id=5, guild=guild)
    first, second = await asyncio.gather(tracker.lookup(member), tracker.lookup(member))
    assert first is second and first.inviter_id == 7 and calls == [1]
    tracker.deleted(invite("aaa", 1))
    assert tracker._uses[1] == {}


@pytest.fixture
def db(core_db):
    return InvitesDB(core_db.pool)


async def test_records_and_counts(db):
    await db.record_join(1, 10, 7, "aaa")
    await db.record_join(1, 11, 7, "aaa")
    await db.record_join(1, 12, 8, "bbb")
    await db.record_join(1, 13, None, None)
    await db.record_join(1, 10, 7, "aaa")  # rejoined: still counted once
    await db.record_join(2, 14, 7, "zzz")  # another server
    assert sorted(await db.invited_by(1, 7)) == [10, 11]
    assert (await db.join_of(1, 12)).inviter_id == 8
    rows = top_inviters(await db.all_joins(1), lambda user_id: user_id != 11)  # 11 has left
    assert rows == [(7, 2, 1), (8, 1, 1)]
