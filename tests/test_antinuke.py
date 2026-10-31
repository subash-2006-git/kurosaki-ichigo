"""Anti-nuke: limits, the sliding window, trust, dangerous permissions, snapshots, and settings storage."""

from types import SimpleNamespace

import discord
import pytest

from features.antinuke.db import AntinukeDB
from features.antinuke.detector import (
    DEFAULT_LIMITS,
    Tracker,
    dangerous_permissions_added,
    format_limit,
    is_trusted,
    merged_limits,
    parse_limit,
    restore_overwrites,
)

LIMIT = {"count": 3, "seconds": 10}


def test_window_trips_on_count_plus_one_inside_the_window():
    t = Tracker()
    assert [t.add(1, 7, "channel_delete", i, LIMIT, now=float(i)) for i in range(4)] == [False, False, False, True]


def test_window_forgets_old_actions():
    t = Tracker()
    for i in range(3):
        t.add(1, 7, "ban", i, LIMIT, now=float(i))
    assert not t.add(1, 7, "ban", 99, LIMIT, now=30.0)  # the first three are older than 10s


def test_window_is_per_actor_and_action():
    t = Tracker()
    for i in range(3):
        t.add(1, 7, "ban", i, LIMIT, now=0.0)
    assert not t.add(1, 8, "ban", 9, LIMIT, now=0.0)
    assert not t.add(1, 7, "kick", 9, LIMIT, now=0.0)
    assert not t.add(2, 7, "ban", 9, LIMIT, now=0.0)


def test_recent_targets_and_one_response_per_nuke():
    t = Tracker()
    t.add(1, 7, "channel_delete", 100, LIMIT, now=0.0)
    t.add(1, 7, "channel_delete", 101, LIMIT, now=1.0)
    t.add(1, 7, "ban", 55, LIMIT, now=2.0)
    assert t.recent(1, 7, 120, now=5.0) == {"channel_delete": [100, 101], "ban": [55]}
    assert t.claim(1, 7, now=0.0) and not t.claim(1, 7, now=30.0) and t.claim(1, 7, now=61.0)


def test_limits_parse_format_and_merge():
    assert parse_limit(" 4 / 20 ", "Bans") == {"count": 4, "seconds": 20}
    for bad in ("4", "a/b", "0/10", "4/0", "51/10"):
        with pytest.raises(ValueError):
            parse_limit(bad, "Bans")
    assert format_limit({"count": 3, "seconds": 10}) == "3/10"
    merged = merged_limits({"ban": {"count": 9}, "unknown": {"count": 1}})
    assert merged["ban"] == {"count": 9, "seconds": 10} and "unknown" not in merged
    assert merged["kick"] == DEFAULT_LIMITS["kick"] and DEFAULT_LIMITS["ban"]["count"] == 3  # defaults untouched


def test_dangerous_permissions_added():
    before = discord.Permissions(send_messages=True, kick_members=True)
    after = discord.Permissions(send_messages=True, kick_members=True, administrator=True, manage_roles=True)
    assert dangerous_permissions_added(before, after) == ["administrator", "manage_roles"]
    assert dangerous_permissions_added(after, before) == []


def test_trust():
    settings = SimpleNamespace(trusted_user_ids=[5], trusted_role_ids=[50])
    trusted = lambda uid, roles=(): is_trusted(uid, set(roles), owner_id=1, bot_id=2, settings=settings)
    assert trusted(1) and trusted(2) and trusted(5) and trusted(9, [50])
    assert not trusted(9, [51])


class Thing:
    def __init__(self, id):
        self.id = id


def test_restore_overwrites_maps_recreated_roles():
    new_role, member = Thing(900), Thing(7)
    guild = SimpleNamespace(get_role=lambda i: new_role if i == 900 else None, get_member=lambda i: member if i == 7 else None)
    snapshot = [
        {"id": 100, "role": 1, "allow": discord.Permissions(view_channel=True).value, "deny": 0},  # deleted role, recreated as 900
        {"id": 7, "role": 0, "allow": 0, "deny": discord.Permissions(send_messages=True).value},
        {"id": 555, "role": 1, "allow": 0, "deny": 0},  # gone for good: skipped
    ]
    out = restore_overwrites(guild, snapshot, {100: 900})
    assert out[new_role].view_channel is True and out[member].send_messages is False and len(out) == 2


async def test_settings_storage_and_cache(core_db):
    db = AntinukeDB(core_db.pool)
    s = await db.settings(1)
    assert (s.enabled, s.punishment, s.restore, s.block_bot_add) == (False, "strip_roles", True, True)
    assert s.limits == merged_limits({})
    s = await db.update(1, enabled=True, punishment="ban", trusted_user_ids=[5], limits={"ban": {"count": 9, "seconds": 30}})
    assert s.enabled and s.punishment == "ban" and s.trusted_user_ids == [5]
    assert s.limits["ban"] == {"count": 9, "seconds": 30} and s.limits["kick"] == DEFAULT_LIMITS["kick"]
    with pytest.raises(ValueError):
        await db.update(1, counter=2)
