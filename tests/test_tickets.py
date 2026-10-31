"""Tickets: channel naming, permissions, transcripts, staff checks, and the database layer."""

from datetime import datetime, timezone
from types import SimpleNamespace

import discord
import pytest

from features.tickets.buttons import channel_name, is_staff, overwrites_for, transcript_text
from features.tickets.db import TicketDB


def test_channel_name():
    assert channel_name(1) == "ticket-0001" and channel_name(12345) == "ticket-12345"


def test_overwrites_hide_from_everyone_and_let_opener_and_staff_talk():
    everyone, opener, staff, me = "everyone", "opener", "staff", "me"
    ow = overwrites_for(everyone, opener, [staff], me)
    assert ow[everyone].view_channel is False
    for who in (opener, staff, me):
        assert ow[who].view_channel and ow[who].send_messages and ow[who].read_message_history
    assert ow[me].manage_channels and not ow[opener].manage_channels


def msg(author, content, minute, attachments=(), embeds=()):
    return SimpleNamespace(author=_Author(author), content=content,
                           created_at=datetime(2026, 10, 31, 12, minute, tzinfo=timezone.utc),
                           attachments=list(attachments), embeds=list(embeds))


class _Author:
    def __init__(self, user_id):
        self.id = user_id

    def __str__(self):
        return f"user{self.id}"


def test_transcript_text():
    ticket = SimpleNamespace(number=7, opener_id=5, reason=None, created_at=datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc))
    text = transcript_text(ticket, "Island", [
        msg(5, "hello", 1),
        msg(9, "", 2, attachments=[SimpleNamespace(url="https://cdn/x.png")]),
    ], "staff (9)")
    assert text.startswith("Ticket #7 — Island\nOpened by user 5 at 2026-10-31 12:00 UTC\nReason: (none given)")
    assert "[2026-10-31 12:01] user5 (5): hello" in text
    assert "[2026-10-31 12:02] user9 (9): https://cdn/x.png" in text
    assert transcript_text(ticket, "Island", [], "x").rstrip().endswith("(no messages)")


def test_is_staff():
    settings = SimpleNamespace(staff_role_ids=[50])
    member = lambda roles, manage=False: SimpleNamespace(roles=[SimpleNamespace(id=r) for r in roles],
                                                         guild_permissions=discord.Permissions(manage_channels=manage))
    assert is_staff(member([50]), settings)
    assert is_staff(member([], manage=True), settings)
    assert not is_staff(member([1, 2]), settings)


@pytest.fixture
def tickets(core_db):
    return TicketDB(core_db.pool)


async def test_settings_defaults_and_update(tickets):
    s = await tickets.settings(1)
    assert (s.enabled, s.max_open, s.ask_reason, s.button_label, s.staff_role_ids) == (False, 1, True, "Open a ticket", [])
    s = await tickets.update_settings(1, enabled=True, staff_role_ids=[10, 11], opening_embed={"title": "Hi"})
    assert s.enabled and s.staff_role_ids == [10, 11] and s.opening_embed == {"title": "Hi"}
    with pytest.raises(ValueError):
        await tickets.update_settings(1, counter=99)


async def test_ticket_lifecycle(tickets):
    assert [await tickets.next_number(1) for _ in range(3)] == [1, 2, 3]
    assert await tickets.next_number(2) == 1  # numbered per server
    t = await tickets.create(1, 4, 100, 5, "help")
    assert (await tickets.by_channel(100)).id == t.id and len(await tickets.open_for(1, 5)) == 1
    assert await tickets.claim(t.id, 9) and not await tickets.claim(t.id, 10)  # first claim wins
    assert await tickets.open_count(1) == 1
    assert await tickets.close(t.id, 9) and not await tickets.close(t.id, 9)  # closing twice is a no-op
    assert await tickets.open_for(1, 5) == [] and await tickets.open_count(1) == 0


async def test_manually_deleted_channel_frees_the_member(tickets):
    t = await tickets.create(1, 1, 200, 5, None)
    await tickets.forget_channel(200)
    assert (await tickets.by_channel(200)).status == "closed" and await tickets.open_for(1, 5) == []
    assert t.status == "open"
