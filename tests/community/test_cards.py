import io

import pytest
from PIL import Image

from features.community.services import cards
from features.community.services.cards import GREETING_SIZE, _faces, _runs, render_greeting_card
from tests.community.fakes import png_bytes
from tests.community.test_media import gif_bytes


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def _render(*args, **kwargs) -> Image.Image:
    card = render_greeting_card(*args, **kwargs)
    image = _open(card.data)
    assert card.extension == image.format.lower()
    return image


def test_greeting_card_is_png_of_expected_size():
    image = _render("welcome", png_bytes(), "Welcome", "Subash", "Member #1,284")
    assert image.format == "PNG"
    assert image.size == GREETING_SIZE


def test_missing_or_broken_avatar_uses_placeholder():
    for avatar in (None, b"not an image"):
        assert _render("goodbye", avatar, "Goodbye", "Subash", "Bye").size == GREETING_SIZE


def test_very_long_and_unicode_names_render():
    name = "Ꭺɴɢᴇʟ 💓 " * 20
    assert _render("welcome", png_bytes(), "Welcome", name, "x" * 300).size == GREETING_SIZE


def test_unknown_kind_falls_back_to_default_background():
    assert _render("other", None, "Hi", "Name", "Sub").size == GREETING_SIZE


def test_static_background_gives_png():
    image = _render("welcome", None, "Welcome", "Subash", "Sub", background=png_bytes((200, 30, 30)))
    assert image.format == "PNG" and image.size == GREETING_SIZE
    # The corner (outside the dark panel) shows the red background, not the default blue gradient.
    r, g, b = image.convert("RGB").getpixel((3, 3))
    assert r > 150 and g < 80 and b < 80


def test_broken_background_falls_back_to_default():
    assert _render("welcome", None, "Welcome", "Subash", "Sub", background=b"garbage").format == "PNG"


def test_animated_background_gives_animated_gif_with_timing():
    background = gif_bytes(frames=6)  # 6 frames x 50 ms
    image = _render("welcome", png_bytes(), "Welcome", "Subash", "Sub", background=background)
    assert image.format == "GIF" and image.is_animated
    assert image.n_frames == 6
    width, height = image.size
    assert width / height == pytest.approx(GREETING_SIZE[0] / GREETING_SIZE[1], rel=0.01)
    total = 0
    for index in range(image.n_frames):
        image.seek(index)
        total += image.info["duration"]
    assert total == 300


def test_long_gifs_are_sampled_without_changing_total_duration():
    background = gif_bytes(frames=150)  # 150 x 50 ms = 7.5 s
    image = _render("welcome", None, "Welcome", "Subash", "Sub", background=background)
    assert image.n_frames <= cards.ANIMATED_ATTEMPTS[0][1]
    total = 0
    for index in range(image.n_frames):
        image.seek(index)
        total += image.info["duration"]
    assert total == 7500


def test_animated_card_shrinks_until_under_limit(monkeypatch):
    sizes = []
    real_encode = cards._encode_animated

    def encode(source, overlay, size, max_frames):
        sizes.append(size)
        return real_encode(source, overlay, size, max_frames)

    monkeypatch.setattr(cards, "_encode_animated", encode)
    monkeypatch.setattr(cards, "MAX_ANIMATED_BYTES", 1)  # nothing fits: every attempt is tried
    card = render_greeting_card("welcome", None, "Welcome", "Subash", "Sub", background=gif_bytes())
    assert card.extension == "gif"
    assert len(sizes) == len(cards.ANIMATED_ATTEMPTS)
    assert sizes == sorted(sizes, reverse=True)


@pytest.mark.parametrize(
    "name",
    [
        "ʟᴏʀᴅ ᴀɪᴢᴇɴ ✓",  # small caps + check mark
        "Ꭺɴɢᴇʟ 💓",  # Cherokee/small caps + emoji
        "𝓢𝓾𝓫𝓪𝓼𝓱 ★ 𝐁𝐨𝐥𝐝 ❤️‍🔥",  # math script/bold letters, star, ZWJ emoji sequence
        "Ｆｕｌｌｗｉｄｔｈ · Ελληνικά · Кириллица",  # fullwidth, Greek, Cyrillic
        "Member #577 of 𓆩・𝐋𝐎𝐒𝐓 𝐈𝐒𝐋𝐀𝐍𝐃・𓆪",  # hieroglyph frame + katakana middle dot
        "꧁༒☬ Ꮮᴏʀᴅ ☬༒꧂ 【VIP】「hi」",  # Javanese frame and CJK brackets
    ],
)
@pytest.mark.parametrize("weight", ["bold", "regular"])
def test_fancy_names_have_no_missing_glyphs(name, weight):
    faces = {str(face.path): face for face in _faces(weight)}
    assert faces, "bundled fonts in assets/fonts are missing"
    for run in _runs(name, weight, 38):
        face = faces[run.font.path]
        missing = [c for c in run.text if ord(c) not in face.codepoints]
        assert not missing, f"{missing!r} would render as boxes"


def test_fullwidth_falls_back_to_plain_letters():
    assert "".join(run.text for run in _runs("Ｆｕｌｌ", "bold", 38)) == "Full"


def test_fallback_fonts_get_faux_bold_only_when_bold():
    bold_runs = _runs("Aizen ✓", "bold", 38)
    assert [run.faux_bold for run in bold_runs] == [False, True]
    assert not any(run.faux_bold for run in _runs("Aizen ✓", "regular", 38))


def test_cjk_punctuation_becomes_lookalikes():
    text = "".join(run.text for run in _runs("・LOST・【VIP】", "regular", 26))
    assert text == "·LOST·[VIP]"


def test_missing_characters_are_logged_once(caplog):
    import logging

    from features.community.services import cards

    cards._reported_missing.discard(0x4E16)
    with caplog.at_level(logging.WARNING, logger="bot.services.cards"):
        _runs("世", "regular", 26)
        _runs("世世", "regular", 26)
    messages = [r.message for r in caplog.records if "U+4E16" in r.message]
    assert len(messages) == 1 and "CJK UNIFIED IDEOGRAPH-4E16" in messages[0]
