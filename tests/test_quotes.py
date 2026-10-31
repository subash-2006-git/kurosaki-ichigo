import io
import re

import pytest
from PIL import Image

from features.quotes.cog import QuoteButton, QuotesDB, quote_view
from features.quotes.render import MAX_CHARS, QuoteStyle, clean_text, layout, render_quote, wrap

NAMES = dict(user=lambda i: {1: "Sam"}.get(i), role=lambda i: {2: "Mods"}.get(i), channel=lambda i: {3: "general"}.get(i))


def test_clean_text():
    assert clean_text("hi <@1> and <@!9> in <#3> for <@&2> <:pog:123> <a:dance:456>", **NAMES) == \
        "hi @Sam and @someone in #general for @Mods :pog: :dance:"
    assert clean_text("a\n\n\n\n\nb", **NAMES) == "a\n\nb"
    long = clean_text("x" * 900, **NAMES)
    assert len(long) == MAX_CHARS and long.endswith("…")


def test_style_bits_round_trip():
    for bits in range(8):
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
    assert ids == ["quote:colour:2:11:22:33", "quote:flip:2:11:22:33", "quote:light:2:11:22:33", "quote:delete:2:11:22:33"]
    match = re.fullmatch(QuoteButton.__discord_ui_compiled_template__, ids[1])
    assert match["action"] == "flip" and match["bits"] == "2" and match["msg"] == "33"
    assert all(len(i) <= 100 for i in [f"quote:delete:7:{10**19}:{10**19}:{10**19}"])


async def test_settings_default_on(core_db):
    db = QuotesDB(core_db.pool)
    assert await db.enabled(1)
    await db.set_enabled(1, False)
    assert not await db.enabled(1) and not await QuotesDB(core_db.pool).enabled(1)
