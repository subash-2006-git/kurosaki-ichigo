"""Suggestions: votes, decisions, the posted message, and restart-proof button ids."""

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from features.suggestions.cog import DecideButton, VoteButton, suggestion_embed, suggestion_view
from features.suggestions.db import SuggestionsDB
from features.suggestions.panel import SuggestionsEditor


@pytest.fixture
def db(core_db):
    return SuggestionsDB(core_db.pool)


async def test_settings_defaults_and_update(db):
    s = await db.settings(1)
    assert (s.enabled, s.channel_id, s.threads) == (False, None, False)
    s = await db.update(1, enabled=True, channel_id=5)
    assert (s.enabled, s.channel_id) == (True, 5)
    with pytest.raises(ValueError):
        await db.update(1, counter=3)


async def test_numbered_per_server(db):
    a = await db.create(1, 7, 5, "first")
    b = await db.create(1, 8, 5, "second")
    c = await db.create(2, 7, 9, "elsewhere")
    assert (a.number, b.number, c.number) == (1, 2, 1)
    await db.set_message(a.id, 100)
    assert (await db.get(a.id)).message_id == 100


async def test_votes_toggle_and_switch(db):
    s = await db.create(1, 7, 5, "idea")
    assert await db.vote(s.id, 10, 1) == (1, 0, 1)
    assert await db.vote(s.id, 11, -1) == (1, 1, -1)
    assert await db.vote(s.id, 10, -1) == (0, 2, -1)  # switched
    assert await db.vote(s.id, 10, -1) == (0, 1, 0)   # pressed again: taken back
    assert await db.tally(s.id) == (0, 1)


async def test_decide_only_once_and_counts(db):
    s = await db.create(1, 7, 5, "idea")
    await db.create(1, 7, 5, "another")
    decided = await db.decide(s.id, "accepted", 9, "Good one")
    assert (decided.status, decided.decided_by, decided.decision_reason) == ("accepted", 9, "Good one")
    assert await db.decide(s.id, "denied", 9, None) is None
    assert await db.counts(1) == {"open": 1, "accepted": 1, "denied": 0}


async def test_deleted_message_drops_the_suggestion_and_votes(db, core_db):
    s = await db.create(1, 7, 5, "idea")
    await db.set_message(s.id, 100)
    await db.vote(s.id, 10, 1)
    assert await db.forget_message(100) and not await db.forget_message(100)
    assert await db.get(s.id) is None
    assert await core_db.pool.fetchval("SELECT COUNT(*) FROM suggestions.votes") == 0


def suggestion(**changes):
    base = dict(id=3, number=12, author_id=7, content="Add a music channel", status="open", decided_by=None,
                decision_reason=None, created_at=datetime(2026, 10, 31, tzinfo=timezone.utc))
    return SimpleNamespace(**{**base, **changes})


def test_embed_open_and_decided():
    embed = suggestion_embed(suggestion(), 4, 1, None, None)
    assert embed.title == "💡 Suggestion #12" and embed.description == "Add a music channel"
    assert embed.fields[0].value == "👍 **4** · 👎 **1**"
    assert embed.author.name == "User 7"
    denied = suggestion_embed(suggestion(status="denied", decided_by=9, decision_reason="Not now"), 0, 3, None, None)
    assert denied.fields[1].value == "❌ **Denied** by <@9>\nNot now"
    assert denied.footer.text == "Voting closed · denied"


def test_view_buttons_and_ids():
    view = suggestion_view(3, 4, 1)
    assert [b.custom_id for b in view.children] == ["suggest:vote:3:up", "suggest:vote:3:down",
                                                    "suggest:accept:3", "suggest:deny:3"]
    assert [b.item.label for b in view.children] == ["4", "1", "Accept", "Deny"]
    closed = suggestion_view(3, 4, 1, is_open=False)
    assert len(closed.children) == 2 and all(b.item.disabled for b in closed.children)


async def test_buttons_rebuild_from_custom_id():
    match = re.fullmatch(VoteButton.__discord_ui_compiled_template__, "suggest:vote:3:down")
    vote = await VoteButton.from_custom_id(None, None, match)
    assert (vote.suggestion_id, vote.vote) == (3, -1)
    match = re.fullmatch(DecideButton.__discord_ui_compiled_template__, "suggest:deny:3")
    decide = await DecideButton.from_custom_id(None, None, match)
    assert (decide.suggestion_id, decide.decision) == (3, "deny")


async def test_panel_builds(db):
    bot = SimpleNamespace(suggestions_db=db, theme=SimpleNamespace(get=lambda guild: SimpleNamespace(colour=1)))
    editor = SuggestionsEditor(bot, SimpleNamespace(id=1, get_channel=lambda channel_id: None), 1)
    await editor.build()
    assert len(list(editor.walk_children())) <= 40
