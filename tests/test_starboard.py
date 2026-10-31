from types import SimpleNamespace

import pytest

from features.starboard.board import decide, first_image
from features.starboard.db import StarboardDB
from features.starboard.leaderboard import leaderboard_embed
from features.starboard.panel import parse_emoji


@pytest.mark.parametrize(("stars", "on_board", "previous", "expected"), [
    (3, False, None, "post"),
    (2, False, None, "none"),
    (4, True, 3, "edit"),
    (3, True, 3, "none"),
    (2, True, 3, "delete"),
    (0, True, 5, "delete"),
])
def test_decide(stars, on_board, previous, expected):
    assert decide(stars, 3, on_board, previous) == expected


def test_first_image_prefers_image_attachments():
    att = lambda name, kind: SimpleNamespace(filename=name, url=f"https://cdn/{name}", content_type=kind)
    msg = SimpleNamespace(attachments=[att("a.txt", "text/plain"), att("b.png", "image/png")], embeds=[])
    assert first_image(msg) == "https://cdn/b.png"
    gif = SimpleNamespace(image=None, type="gifv", thumbnail=SimpleNamespace(url="https://tenor/x.gif"))
    assert first_image(SimpleNamespace(attachments=[], embeds=[gif])) == "https://tenor/x.gif"
    assert first_image(SimpleNamespace(attachments=[], embeds=[])) is None


def test_parse_emoji():
    guild = SimpleNamespace(get_emoji=lambda i: object() if i == 123456789012345678 else None, emojis=[])
    assert parse_emoji(" ⭐ ", guild) == "⭐"
    assert parse_emoji("<:shine:123456789012345678>", guild) == "<:shine:123456789012345678>"
    for bad in ("", "star", "<:other:987654321098765432>"):
        with pytest.raises(ValueError):
            parse_emoji(bad, guild)


async def test_db(core_db):
    db = StarboardDB(core_db.pool)
    s = await db.settings(1)
    assert (s.enabled, s.emoji, s.threshold, s.allow_self) == (False, "⭐", 3, False)
    s = await db.update(1, enabled=True, channel_id=9, threshold=5)
    assert s.enabled and s.channel_id == 9 and s.threshold == 5
    await db.save_entry(100, 1, 2, 7, 500, 5)
    await db.save_entry(100, 1, 2, 7, 501, 6)
    e = await db.entry(100)
    assert (e.star_message_id, e.stars) == (501, 6) and len(await db.entries_by_author(7)) == 1
    await db.delete_entry(100)
    assert await db.entry(100) is None


async def test_leaderboard(core_db):
    db = StarboardDB(core_db.pool)
    for message_id, author, stars in ((1, 7, 5), (2, 7, 3), (3, 8, 6), (4, 9, 2)):
        await db.save_entry(message_id, 1, 50, author, 1000 + message_id, stars)
    await db.save_entry(5, 2, 50, 9, 1005, 99)  # another server
    authors = await db.top_authors(1)
    assert [(a.author_id, a.stars, a.posts) for a in authors] == [(7, 8, 2), (8, 6, 1), (9, 2, 1)]
    assert [m.message_id for m in await db.top_messages(1, limit=2)] == [3, 1]

    embed = leaderboard_embed(authors, await db.top_messages(1, limit=2), "⭐", 1)
    assert embed.fields[0].value.splitlines()[0] == "🥇 <@7> · **8** ⭐ (2 posts)"
    assert embed.fields[1].value.splitlines()[0] == "**6** ⭐ · <@8> · [jump](https://discord.com/channels/1/50/3)"
    assert leaderboard_embed([], [], "⭐", 1).description == "Nothing on the board yet."
