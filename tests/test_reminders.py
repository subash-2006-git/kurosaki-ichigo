"""Reminders: time parsing, the per-member limit, at-most-once delivery, and where they're sent."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from features.reminders.cog import Reminders, parse_delay, short
from features.reminders.db import MAX_PER_USER, ReminderDB


def test_parse_delay():
    assert parse_delay("1d12h") == timedelta(days=1, hours=12)
    for bad, message in [("soon", "Use a time"), ("30s", "at least a minute"), ("60w", "at most a year")]:
        with pytest.raises(ValueError, match=message):
            parse_delay(bad)


def test_short_label():
    assert short(SimpleNamespace(id=4, text="x" * 80)) == "#4 · " + "x" * 59 + "…"


@pytest.fixture
def db(core_db):
    return ReminderDB(core_db.pool)


def later(minutes):
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def test_add_list_cancel_and_limit(db):
    first = await db.add(7, 1, 2, "stretch", False, later(30))
    assert db.next_due == first.due_at
    soon = await db.add(7, 1, 2, "water", True, later(5))
    assert db.next_due == soon.due_at  # the earliest is remembered
    assert [r.text for r in await db.for_user(7)] == ["water", "stretch"]
    assert not await db.cancel(8, first.id)  # not theirs
    assert await db.cancel(7, first.id)
    for n in range(MAX_PER_USER - 1):
        assert await db.add(7, None, None, f"r{n}", True, later(60))
    assert await db.add(7, None, None, "too many", True, later(60)) is None
    assert await db.add(8, None, None, "someone else", True, later(60))


async def test_due_reminders_are_taken_once(db, core_db):
    a = await db.add(7, 1, 2, "a", False, later(10))
    await db.add(7, 1, 2, "b", False, later(60))
    await core_db.pool.execute("UPDATE reminders.reminders SET due_at = NOW() - INTERVAL '1 minute' WHERE id = $1", a.id)
    [taken] = await db.take_due()
    assert taken.text == "a"
    assert await db.take_due() == []  # gone: never delivered twice
    await db.refresh_next_due()
    assert db.loaded and db.next_due > datetime.now(timezone.utc)


class Target:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, content=None, **kwargs):
        if self.fail:
            raise discord.HTTPException(SimpleNamespace(status=403, reason="Forbidden"), "no")
        self.sent.append((content, kwargs))


def setup_cog(channel_ok=True, channel_fails=False):
    channel, dm = Target(fail=channel_fails), Target()
    perms = SimpleNamespace(send_messages=channel_ok, embed_links=True, view_channel=True)
    channel.permissions_for = lambda who: perms
    guild = SimpleNamespace(id=1, me=object(), get_channel_or_thread=lambda i: channel if i == 2 else None,
                            get_member=lambda i: object())
    bot = SimpleNamespace(get_guild=lambda i: guild if i == 1 else None, get_user=lambda i: dm)
    cog = Reminders.__new__(Reminders)
    cog.bot = bot
    return cog, channel, dm


def reminder(**changes):
    base = dict(id=1, user_id=7, guild_id=1, channel_id=2, text="stretch", private=False, jump_url=None,
                created_at=datetime(2026, 10, 31, tzinfo=timezone.utc))
    return SimpleNamespace(**{**base, **changes})


async def test_sent_in_the_channel_with_a_ping():
    cog, channel, dm = setup_cog()
    assert await cog.send(reminder())
    [(content, kwargs)] = channel.sent
    assert content == "<@7>" and kwargs["embed"].description == "stretch" and dm.sent == []


async def test_private_or_unpostable_goes_to_dms():
    cog, channel, dm = setup_cog()
    assert await cog.send(reminder(private=True))
    assert channel.sent == [] and len(dm.sent) == 1
    cog, channel, dm = setup_cog(channel_ok=False)
    assert await cog.send(reminder())
    assert channel.sent == [] and len(dm.sent) == 1
    cog, channel, dm = setup_cog(channel_fails=True)
    assert await cog.send(reminder())
    assert len(dm.sent) == 1
