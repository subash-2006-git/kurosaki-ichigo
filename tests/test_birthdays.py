"""Birthdays: dates (29 February too), the once-a-day claim, the role hand-over and the message."""

from datetime import date, datetime
from types import SimpleNamespace

import discord
import pytest

from features.birthdays.cog import Birthdays, due, fill, format_day, next_occurrence, valid_day
from features.birthdays.db import BirthdayDB
from features.birthdays.panel import BirthdaysEditor, parse_zone


def test_valid_days():
    assert valid_day(2, 29) and valid_day(12, 31)
    assert not valid_day(2, 30) and not valid_day(4, 31) and not valid_day(13, 1)
    assert format_day(10, 31) == "31 October"


def test_next_occurrence():
    today = date(2026, 10, 31)
    assert next_occurrence(10, 31, today) == today
    assert next_occurrence(11, 1, today) == date(2026, 11, 1)
    assert next_occurrence(1, 5, today) == date(2027, 1, 5)
    assert next_occurrence(2, 29, today) == date(2027, 2, 28)       # not a leap year
    assert next_occurrence(2, 29, date(2027, 3, 1)) == date(2028, 2, 29)


def test_fill_message():
    assert fill(None, ["<@1>"], "Island") == "Happy birthday <@1>! 🎉"
    assert fill("{users} · {server}", ["<@1>", "<@2>", "<@3>"], "Island") == "<@1>, <@2> and <@3> · Island"


def test_due_once_a_day_after_the_hour():
    settings = SimpleNamespace(last_run=date(2026, 10, 30), hour=9)
    assert not due(settings, datetime(2026, 10, 31, 8, 59))
    assert due(settings, datetime(2026, 10, 31, 9, 0))
    settings.last_run = date(2026, 10, 31)
    assert not due(settings, datetime(2026, 10, 31, 23, 0))


def test_parse_zone():
    assert parse_zone("utc") == "UTC" and parse_zone("Asia/Kolkata") == "Asia/Kolkata"
    with pytest.raises(ValueError):
        parse_zone("Mars/Olympus")


@pytest.fixture
def db(core_db):
    return BirthdayDB(core_db.pool)


async def test_birthdays_on_a_day(db):
    await db.set(1, 10, 10, 31)
    await db.set(1, 11, 2, 29)
    await db.set(1, 12, 2, 28)
    await db.set(2, 13, 10, 31)  # another server
    assert await db.on(1, date(2026, 10, 31)) == [10]
    assert await db.on(1, date(2027, 2, 28)) == [11, 12]  # 29 Feb counts on the 28th in other years
    assert await db.on(1, date(2028, 2, 28)) == [12]
    assert await db.on(1, date(2028, 2, 29)) == [11]
    await db.set(1, 10, 1, 1)  # changed
    assert await db.on(1, date(2026, 10, 31)) == []
    assert await db.remove(1, 10) and not await db.remove(1, 10)


async def test_enabled_cache_and_daily_claim(db):
    assert await db.enabled_settings() == []
    await db.update(1, enabled=True, timezone="Asia/Kolkata", hour=9)
    [settings] = await db.enabled_settings()
    assert (settings.timezone, settings.hour, settings.last_run) == ("Asia/Kolkata", 9, None)
    today = date(2026, 10, 31)
    assert await db.claim_day(1, today) and not await db.claim_day(1, today)  # once a day
    assert (await db.enabled_settings())[0].last_run == today  # the cache follows
    assert await db.claim_day(1, date(2026, 11, 1))
    await db.update(1, enabled=False)
    assert await db.enabled_settings() == []
    with pytest.raises(ValueError):
        await db.update(1, last_run=today)


class FakeMember(SimpleNamespace):
    async def add_roles(self, role, reason=None):
        self.roles.append(role)
        role.members.append(self)

    async def remove_roles(self, role, reason=None):
        self.roles.remove(role)
        role.members.remove(self)


class FakeChannel(discord.abc.Messageable):
    def __init__(self):
        self.sent = []

    async def _get_channel(self):
        return self

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


async def test_celebrate_moves_the_role_and_announces(db):
    role = SimpleNamespace(id=50, name="Birthday", members=[], is_default=lambda: False, managed=False, position=1)
    yesterday = FakeMember(id=1, bot=False, roles=[role], mention="<@1>", display_avatar=SimpleNamespace(url="a"))
    role.members.append(yesterday)
    today_member = FakeMember(id=2, bot=False, roles=[], mention="<@2>", display_avatar=SimpleNamespace(url="b"))
    channel = FakeChannel()
    me = SimpleNamespace(guild_permissions=discord.Permissions(manage_roles=True), top_role=SimpleNamespace(position=10))
    members = {1: yesterday, 2: today_member}
    guild = SimpleNamespace(id=1, name="Island", me=me, get_member=members.get, get_role=lambda i: role if i == 50 else None,
                            get_channel=lambda i: channel if i == 60 else None)
    await db.set(1, 2, 10, 31)
    cog = Birthdays.__new__(Birthdays)
    cog.bot = SimpleNamespace(birthdays_db=db)
    settings = SimpleNamespace(role_id=50, channel_id=60, message=None)
    await cog.celebrate(guild, settings, date(2026, 10, 31))
    assert role.members == [today_member] and yesterday.roles == []
    [(content, kwargs)] = channel.sent
    assert content == "<@2>" and kwargs["embed"].description == "Happy birthday <@2>! 🎉"

    channel.sent.clear()
    await cog.celebrate(guild, settings, date(2026, 11, 1))  # nobody today: role taken back, no message
    assert role.members == [] and channel.sent == []


async def test_panel_builds(db):
    bot = SimpleNamespace(birthdays_db=db, theme=SimpleNamespace(get=lambda guild: SimpleNamespace(colour=1)))
    guild = SimpleNamespace(id=1, get_channel=lambda i: None, get_role=lambda i: None)
    editor = BirthdaysEditor(bot, guild, 1)
    await editor.build()
    assert len(list(editor.walk_children())) <= 40
