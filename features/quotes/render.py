"""Quote images: the author's avatar fading into the message text, with their name underneath.

Reuses the community card renderer's font chain (with emoji/symbol fallback) and text drawing.
"""

from __future__ import annotations

import io
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image, ImageOps

from features.community.services.cards import _circle_avatar, _draw_text, _runs, _width

log = logging.getLogger(__name__)

SIZE = (1200, 630)
AVATAR = 630
MAX_CHARS = 500
TEXT_SIZES = range(64, 21, -2)
LINE_SPACING = 1.3


@dataclass(frozen=True)
class QuoteStyle:
    colour: bool = False  # avatar in colour (default: grayscale)
    flip: bool = False    # avatar on the right
    light: bool = False   # light background
    tint: bool = False    # background tinted with the server's theme colour

    @property
    def bits(self) -> int:
        return int(self.colour) | int(self.flip) << 1 | int(self.light) << 2 | int(self.tint) << 3

    @classmethod
    def from_bits(cls, bits: int) -> QuoteStyle:
        return cls(colour=bool(bits & 1), flip=bool(bits & 2), light=bool(bits & 4), tint=bool(bits & 8))

    def toggled(self, option: str) -> QuoteStyle:
        return QuoteStyle(**{**self.__dict__, option: not getattr(self, option)})


# ---- text --------------------------------------------------------------------------

_USER = re.compile(r"<@!?(\d+)>")
_ROLE = re.compile(r"<@&(\d+)>")
_CHANNEL = re.compile(r"<#(\d+)>")
_EMOJI = re.compile(r"<a?:(\w+):\d+>")
_TIMESTAMP = re.compile(r"<t:(\d+)(?::\w)?>")


def clean_text(content: str, *, user: Callable[[int], str | None], role: Callable[[int], str | None],
               channel: Callable[[int], str | None]) -> str:
    """Turn Discord markup into readable text, and cap the length."""
    text = _USER.sub(lambda m: "@" + (user(int(m[1])) or "someone"), content)
    text = _ROLE.sub(lambda m: "@" + (role(int(m[1])) or "role"), text)
    text = _CHANNEL.sub(lambda m: "#" + (channel(int(m[1])) or "channel"), text)
    text = _EMOJI.sub(lambda m: f":{m[1]}:", text)
    text = _TIMESTAMP.sub("a date", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 1].rstrip() + "…"
    return text


def _fits(text: str, size: int, max_width: int) -> bool:
    return _width(_runs(text, "regular", size)) <= max_width


def wrap(text: str, size: int, max_width: int) -> list[str]:
    """Greedy word wrap by measured width; words wider than a line are split by character."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if _fits(candidate, size, max_width):
                line = candidate
                continue
            if line:
                lines.append(line)
            line = ""
            for char in word:  # a single long word: break it up
                if _fits(line + char, size, max_width):
                    line += char
                else:
                    lines.append(line)
                    line = char
        lines.append(line)
    return lines


def layout(text: str, max_width: int, max_height: int) -> tuple[list[str], int]:
    """The largest text size whose wrapped lines fit the box; at the smallest size, extra lines are cut."""
    for size in TEXT_SIZES:
        lines = wrap(text, size, max_width)
        if len(lines) * size * LINE_SPACING <= max_height:
            return lines, size
    size = TEXT_SIZES[-1]
    lines = wrap(text, size, max_width)
    keep = max(1, int(max_height // (size * LINE_SPACING)))
    if len(lines) > keep:
        lines = lines[:keep]
        last = lines[-1]
        while last and not _fits(last + "…", size, max_width):
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines, size


# ---- image -------------------------------------------------------------------------


def _fade_mask(width: int, height: int, flip: bool) -> Image.Image:
    """Opaque on the outer half of the avatar, fading to transparent towards the text."""
    solid = int(width * 0.45)
    row = [255 if x < solid else max(0, int(255 * (1 - (x - solid) / (width - solid)) ** 1.6)) for x in range(width)]
    if flip:
        row.reverse()
    mask = Image.new("L", (width, 1))
    mask.putdata(row)
    return mask.resize((width, height))


def _avatar(avatar_png: bytes | None, colour: bool) -> Image.Image:
    image: Image.Image | None = None
    if avatar_png:
        try:
            with Image.open(io.BytesIO(avatar_png)) as source:
                image = ImageOps.fit(source.convert("RGB"), (AVATAR, AVATAR), Image.Resampling.LANCZOS)
        except OSError:
            log.warning("Could not decode avatar for a quote; using a plain block")
    if image is None:
        image = Image.new("RGB", (AVATAR, AVATAR), (88, 101, 242))
    if not colour:
        image = ImageOps.grayscale(image).convert("RGB")
    return image.convert("RGBA")


def palette(style: QuoteStyle, accent: int | None) -> tuple[tuple[int, int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    """(background, main text, muted text). With `tint`, the background takes on the theme colour."""
    if style.light:
        background, main, muted = (245, 245, 245), (20, 20, 20), (95, 95, 95)
    else:
        background, main, muted = (0, 0, 0), (255, 255, 255), (160, 160, 160)
    if style.tint and accent is not None:
        r, g, b = (accent >> 16) & 255, (accent >> 8) & 255, accent & 255
        mix = 0.82 if style.light else 0.22  # light: a pale wash of the colour; dark: a deep shade of it
        background = tuple(int(c * mix + 255 * (1 - mix)) for c in (r, g, b)) if style.light else \
            tuple(int(c * mix) for c in (r, g, b))
    return (*background, 255), main, muted


def render_quote(text: str, display_name: str, username: str, avatar_png: bytes | None, style: QuoteStyle,
                 accent: int | None = None) -> bytes:
    width, height = SIZE
    background, main, muted = palette(style, accent)
    canvas = Image.new("RGBA", SIZE, background)

    avatar = _avatar(avatar_png, style.colour)
    avatar.putalpha(_fade_mask(AVATAR, AVATAR, style.flip))
    canvas.alpha_composite(avatar, (width - AVATAR if style.flip else 0, 0))

    box_left = 60 if style.flip else AVATAR - 40
    box_right = width - AVATAR + 40 if style.flip else width - 60
    box_width = box_right - box_left
    centre = (box_left + box_right) // 2

    lines, size = layout(text or "…", box_width, 400)
    line_height = int(size * LINE_SPACING)
    name_size, user_size, gap = 30, 24, 28
    block = len(lines) * line_height + gap + name_size + 8 + user_size
    top = max(30, (height - block) // 2)
    for i, line in enumerate(lines):
        _draw_text(canvas, _runs(line, "regular", size), size, centre, top + i * line_height, main, align="center")
    y = top + len(lines) * line_height + gap
    _draw_text(canvas, _runs(f"— {display_name}"[:60], "bold", name_size), name_size, centre, y, main, align="center")
    _draw_text(canvas, _runs(f"@{username}"[:40], "regular", user_size), user_size, centre, y + name_size + 8, muted,
               align="center")

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


# ---- several messages on one image --------------------------------------------------

CONVO_WIDTH = 1200
CONVO_AVATAR = 72
CONVO_TEXT = 30
CONVO_NAME = 28
CONVO_PAD = 48
CONVO_GAP = 30
CONVO_MAX_LINES = 6


@dataclass(frozen=True)
class QuoteLine:
    display_name: str
    username: str
    text: str
    avatar_png: bytes | None


def render_conversation(lines: list[QuoteLine], style: QuoteStyle, accent: int | None = None) -> bytes:
    """A few messages in a row, chat-style: round avatar, name, and wrapped text for each."""
    background, main, muted = palette(style, accent)
    text_left = CONVO_PAD + CONVO_AVATAR + 28
    text_width = CONVO_WIDTH - text_left - CONVO_PAD
    line_height = int(CONVO_TEXT * LINE_SPACING)

    blocks = []
    for line in lines:
        wrapped = wrap(line.text or "…", CONVO_TEXT, text_width)
        if len(wrapped) > CONVO_MAX_LINES:
            wrapped = wrapped[:CONVO_MAX_LINES]
            wrapped[-1] = wrapped[-1].rstrip() + "…"
        body = CONVO_NAME + 14 + len(wrapped) * line_height
        blocks.append((line, wrapped, max(CONVO_AVATAR, body)))
    height = CONVO_PAD * 2 + sum(b[2] for b in blocks) + CONVO_GAP * (len(blocks) - 1)

    canvas = Image.new("RGBA", (CONVO_WIDTH, max(height, 200)), background)
    y = CONVO_PAD
    for line, wrapped, block_height in blocks:
        avatar = _circle_avatar(line.avatar_png, CONVO_AVATAR)
        if not style.colour:
            alpha = avatar.getchannel("A")
            avatar = ImageOps.grayscale(avatar.convert("RGB")).convert("RGBA")
            avatar.putalpha(alpha)
        canvas.alpha_composite(avatar, (CONVO_PAD, y))
        name = f"{line.display_name}"[:50]
        _draw_text(canvas, _runs(name, "bold", CONVO_NAME), CONVO_NAME, text_left, y, main)
        handle_x = text_left + _width(_runs(name, "bold", CONVO_NAME)) + 12
        _draw_text(canvas, _runs(f"@{line.username}"[:40], "regular", 22), 22, handle_x, y + 4, muted)
        for i, text in enumerate(wrapped):
            _draw_text(canvas, _runs(text, "regular", CONVO_TEXT), CONVO_TEXT, text_left, y + CONVO_NAME + 14 + i * line_height, main)
        y += block_height + CONVO_GAP

    out = io.BytesIO()
    canvas.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
