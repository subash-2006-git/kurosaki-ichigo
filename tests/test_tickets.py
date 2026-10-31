"""Tickets: channel naming, permissions, transcripts, staff checks, and the database layer."""

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from features.tickets.buttons import (
    OpenTicketButton,
    RateTicketButton,
    channel_name,
    idle_step,
    is_staff,
    opening_message,
    overwrites_for,
    rating_view,
    staff_role_ids,
    transcript_text,
)
from features.tickets.db import MAX_TYPES, TicketDB
from features.tickets.panel import TicketsEditor, rating_lines


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


# ---- ticket types, auto-close and ratings ----


def test_staff_roles_add_the_type_roles():
    settings = SimpleNamespace(staff_role_ids=[50])
    typed = SimpleNamespace(staff_role_ids=[50, 60])
    assert staff_role_ids(settings) == [50] and staff_role_ids(settings, typed) == [50, 60]
    member = SimpleNamespace(roles=[SimpleNamespace(id=60)], guild_permissions=discord.Permissions.none())
    assert is_staff(member, settings, typed) and not is_staff(member, settings)


def test_opening_message_has_a_button_per_type():
    settings = SimpleNamespace(opening_embed=None, button_label="Open a ticket")
    guild = SimpleNamespace(id=1)
    view = opening_message(settings, guild, ())["view"]
    assert [b.custom_id for b in view.children] == ["ticket:open"]
    types = [SimpleNamespace(id=3, label="Support", emoji="🛟"), SimpleNamespace(id=4, label="Appeal", emoji=None)]
    view = opening_message(settings, guild, types)["view"]
    assert [(b.item.label, b.custom_id) for b in view.children] == [("Support", "ticket:open:3"), ("Appeal", "ticket:open:4")]
    assert str(view.children[1].item.emoji) == "🎫"


@pytest.mark.parametrize(("custom_id", "type_id"), [("ticket:open", None), ("ticket:open:42", 42)])
async def test_open_button_reads_its_type(custom_id, type_id):
    match = re.fullmatch(OpenTicketButton.__discord_ui_compiled_template__, custom_id)
    assert (await OpenTicketButton.from_custom_id(None, None, match)).type_id == type_id


def test_idle_step():
    now = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    hours_ago = lambda h: now - timedelta(hours=h)  # noqa: E731
    message_id = lambda when: discord.utils.time_snowflake(when)  # noqa: E731
    # close after 24h of silence, warning 6h before: warn once it's been quiet 18h
    assert idle_step(now, hours_ago(17), message_id(hours_ago(17)), None, 24, 6) is None
    assert idle_step(now, hours_ago(18), message_id(hours_ago(18)), None, 24, 6) == "warn"
    warning = message_id(hours_ago(5))
    assert idle_step(now, hours_ago(5), warning, warning, 24, 6) is None  # warned, still waiting
    warning = message_id(hours_ago(6))
    assert idle_step(now, hours_ago(6), warning, warning, 24, 6) == "close"
    # someone replied after the warning: back to a normal quiet ticket
    reply = message_id(hours_ago(1))
    assert idle_step(now, hours_ago(1), reply, warning, 24, 6) is None


async def test_types_storage_and_limit(tickets):
    first = await tickets.add_type(1, "Support", "🛟")
    for n in range(MAX_TYPES - 1):
        assert await tickets.add_type(1, f"Type {n}", None)
    assert await tickets.add_type(1, "One too many", None) is None
    assert await tickets.add_type(2, "Other server", None)  # the limit is per server
    updated = await tickets.update_type(1, first.id, staff_role_ids=[7], category_id=8)
    assert (updated.staff_role_ids, updated.category_id) == ([7], 8)
    assert await tickets.update_type(2, first.id, label="x") is None  # not that server's type
    with pytest.raises(ValueError):
        await tickets.update_type(1, first.id, counter=2)
    t = await tickets.create(1, 1, 300, 5, None, first.id)
    assert await tickets.remove_type(1, first.id) and not await tickets.remove_type(1, first.id)
    assert (await tickets.by_id(t.id)).type_id is None  # the ticket keeps working without its type
    assert len(await tickets.types(1)) == MAX_TYPES - 1


async def test_idle_candidates_follow_the_setting(tickets):
    await tickets.create(1, 1, 400, 5, None)
    assert not await tickets.any_idle_close() and await tickets.idle_candidates() == []
    tickets.idle_on = False
    await tickets.update_settings(1, idle_close_hours=24, idle_warn_hours=6)
    assert tickets.idle_on is None  # the loop re-checks after a change
    assert await tickets.any_idle_close()
    [candidate] = await tickets.idle_candidates()
    assert (candidate.channel_id, candidate.idle_close_hours, candidate.idle_warn_hours) == (400, 24, 6)
    await tickets.set_idle_warning(candidate.id, 999)
    assert (await tickets.by_id(candidate.id)).idle_warning_id == 999


async def test_ratings_once_by_the_opener_after_closing(tickets):
    a = await tickets.create(1, 1, 500, 5, None)
    assert not await tickets.rate(a.id, 5, 4)  # still open
    await tickets.claim(a.id, 9)
    await tickets.close(a.id, 9)
    assert not await tickets.rate(a.id, 6, 4)  # someone else
    assert await tickets.rate(a.id, 5, 4) and not await tickets.rate(a.id, 5, 1)  # only once
    b = await tickets.create(1, 2, 501, 6, None)
    await tickets.claim(b.id, 9)
    await tickets.close(b.id, 9)
    await tickets.rate(b.id, 6, 5)
    c = await tickets.create(1, 3, 502, 7, None)
    await tickets.close(c.id, 7)
    await tickets.rate(c.id, 7, 1)
    overall, staff = await tickets.rating_stats(1)
    assert (overall.count, round(overall.average, 2)) == (3, 3.33)
    assert [(s.staff_id, s.count, s.average) for s in staff] == [(9, 2, 4.5)]
    assert rating_lines(overall, staff).splitlines()[1] == "<@9>: **4.5** ⭐ (2)"
    empty, _ = await tickets.rating_stats(2)
    assert rating_lines(empty, []) == "No ratings yet."


async def test_rating_button_ids():
    view = rating_view(12)
    assert [b.custom_id for b in view.children] == [f"ticket:rate:12:{n}" for n in range(1, 6)]
    match = re.fullmatch(RateTicketButton.__discord_ui_compiled_template__, "ticket:rate:12:3")
    button = await RateTicketButton.from_custom_id(None, None, match)
    assert (button.ticket_id, button.stars) == (12, 3)


@pytest.mark.parametrize("page", ["general", "types", "after"])
async def test_panel_pages_fit_discord_limits(tickets, page):
    for n in range(MAX_TYPES):
        added = await tickets.add_type(1, f"Type {n}", "🛟")
    bot = SimpleNamespace(tickets_db=tickets, theme=SimpleNamespace(get=lambda guild: SimpleNamespace(colour=1)))
    guild = SimpleNamespace(id=1, get_channel=lambda channel_id: None)
    editor = TicketsEditor(bot, guild, 1, page)
    editor.type_id = added.id  # the busiest state: a type picked for changing
    await editor.build()
    assert len(list(editor.walk_children())) <= 40
