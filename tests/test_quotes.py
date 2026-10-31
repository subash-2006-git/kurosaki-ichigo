import io
import re
from types import SimpleNamespace

import pytest
from PIL import Image

from features.quotes.cog import (
    ConversationButton,
    QuoteButton,
    QuotesDB,
    conversation_view,
    latest_message,
    latest_messages,
    messages_from,
    parse_message_ref,
    quote_view,
)
from features.quotes.render import MAX_CHARS, QuoteLine, QuoteStyle, clean_text, layout, palette, render_conversation, render_quote, wrap

NAMES = dict(user=lambda i: {1: "Sam"}.get(i), role=lambda i: {2: "Mods"}.get(i), channel=lambda i: {3: "general"}.get(i))


def test_clean_text():
    assert clean_text("hi <@1> and <@!9> in <#3> for <@&2> <:pog:123> <a:dance:456>", **NAMES) == \
        "hi @Sam and @someone in #general for @Mods :pog: :dance:"
    assert clean_text("a\n\n\n\n\nb", **NAMES) == "a\n\nb"
    long = clean_text("x" * 900, **NAMES)
    assert len(long) == MAX_CHARS and long.endswith("…")


def test_style_bits_round_trip():
    for bits in range(16):
        assert QuoteStyle.from_bits(bits).bits == bits
    assert QuoteStyle().toggled("flip") == QuoteStyle(flip=True)
    assert QuoteStyle(colour=True).toggled("colour") == QuoteStyle()


def test_wrap_and_layout():
    lines = wrap("one two three four five six seven", 40, 200)
    assert len(lines) > 1 and " ".join(lines) == "one two three four five six seven"
    assert all(len(line) <= 60 for line in wrap("a" * 200, 40, 300))  # long words are split
    big, big_size = layout("short", 560, 400)
    small, small_size = layout("word " * 200, 560, 400)
    assert big == ["short"] and big_size > small_size and small[-1].endswith("…")


@pytest.mark.parametrize("text", ["hello", "🔥 emoji ✨ text", "word " * 150, ""])
def test_render_returns_png(text):
    png = render_quote(text, "Sam", "sam", None, QuoteStyle(colour=True, flip=True, light=True))
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (1200, 630)


def test_buttons_carry_everything_in_their_id():
    view = quote_view(QuoteStyle(flip=True), 11, 22, 33)
    ids = [item.custom_id for item in view.children]
    assert ids == ["quote:colour:2:11:22:33", "quote:flip:2:11:22:33", "quote:light:2:11:22:33", "quote:tint:2:11:22:33",
                   "quote:delete:2:11:22:33"]
    match = re.fullmatch(QuoteButton.__discord_ui_compiled_template__, ids[1])
    assert match["action"] == "flip" and match["bits"] == "2" and match["msg"] == "33"
    assert all(len(i) <= 100 for i in [f"quote:delete:15:{10**19}:{10**19}:{10**19}",
                                        f"quotes:colour:15:{10**19}:{10**19}:{10**19}:5"])


async def test_settings_default_on(core_db):
    db = QuotesDB(core_db.pool)
    assert await db.enabled(1)
    await db.set_enabled(1, False)
    assert not await db.enabled(1) and not await QuotesDB(core_db.pool).enabled(1)


def test_parse_message_ref():
    assert parse_message_ref("https://discord.com/channels/1/22/333") == (22, 333)
    assert parse_message_ref("https://ptb.discord.com/channels/1/22/333 extra") == (22, 333)
    assert parse_message_ref("https://discordapp.com/channels/1/22/333") == (22, 333)
    assert parse_message_ref(" 333 ") == (None, 333)
    assert parse_message_ref("hello") is None


async def test_latest_message_skips_bots_and_empty():
    def msg(author_id, content, bot=False):
        return SimpleNamespace(author=SimpleNamespace(id=author_id, bot=bot), content=content)

    messages = [msg(1, "bot says", bot=True), msg(2, "   "), msg(3, "from three"), msg(2, "from two")]

    class Channel:
        async def history(self, limit):
            for m in messages:
                yield m

    assert (await latest_message(Channel(), None)).content == "from three"
    assert (await latest_message(Channel(), SimpleNamespace(id=2))).content == "from two"
    assert await latest_message(Channel(), SimpleNamespace(id=9)) is None


def test_conversation_buttons_and_render():
    view = conversation_view(QuoteStyle(tint=True), 11, 22, 33, 3)
    assert [i.custom_id for i in view.children] == [
        "quotes:colour:8:11:22:33:3", "quotes:light:8:11:22:33:3", "quotes:tint:8:11:22:33:3", "quotes:delete:8:11:22:33:3"]
    match = re.fullmatch(ConversationButton.__discord_ui_compiled_template__, "quotes:light:12:11:22:33:5")
    assert match["n"] == "5" and match["bits"] == "12"
    png = render_conversation([QuoteLine("Sam", "sam", "hi " * 80, None), QuoteLine("Lee", "lee", "🔥", None)],
                              QuoteStyle(light=True, tint=True), accent=0x5865F2)
    image = Image.open(io.BytesIO(png))
    assert image.width == 1200 and 200 <= image.height <= 900


def test_tint_changes_the_background_only_when_on():
    assert palette(QuoteStyle(), 0xFF0000)[0] == (0, 0, 0, 255)
    assert palette(QuoteStyle(tint=True), 0xFF0000)[0][0] > 0           # dark red shade
    assert palette(QuoteStyle(light=True, tint=True), 0x0000FF)[0][2] == 255  # pale blue wash
    assert palette(QuoteStyle(tint=True), None)[0] == (0, 0, 0, 255)     # no theme colour: plain


async def test_latest_messages_and_messages_from():
    def msg(i, content="text", bot=False):
        return SimpleNamespace(id=i, author=SimpleNamespace(id=i % 3, bot=bot), content=content)

    newest_first = [msg(5), msg(4, bot=True), msg(3, ""), msg(2), msg(1)]

    class Channel:
        async def history(self, limit, after=None, oldest_first=False):
            items = list(reversed(newest_first)) if oldest_first else newest_first
            for m in items:
                if after is None or m.id > after.id:
                    yield m

    assert [m.id for m in await latest_messages(Channel(), None, 3)] == [1, 2, 5]   # oldest first
    assert [m.id for m in await messages_from(Channel(), msg(1), 3)] == [1, 2, 5]
    assert [m.id for m in await messages_from(Channel(), msg(2), 5)] == [2, 5]


async def test_default_style_saved(core_db):
    db = QuotesDB(core_db.pool)
    assert await db.default_style(1) == QuoteStyle()
    await db.set_default_style(1, QuoteStyle(colour=True, tint=True))
    assert await QuotesDB(core_db.pool).default_style(1) == QuoteStyle(colour=True, tint=True)
    assert await db.enabled(1)
