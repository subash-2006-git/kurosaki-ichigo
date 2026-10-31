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

from features.community.services.cards import _draw_text, _runs, _width

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

    @property
    def bits(self) -> int:
        return int(self.colour) | int(self.flip) << 1 | int(self.light) << 2

    @classmethod
    def from_bits(cls, bits: int) -> QuoteStyle:
        return cls(colour=bool(bits & 1), flip=bool(bits & 2), light=bool(bits & 4))

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


def render_quote(text: str, display_name: str, username: str, avatar_png: bytes | None, style: QuoteStyle) -> bytes:
    width, height = SIZE
    background = (245, 245, 245, 255) if style.light else (0, 0, 0, 255)
    main = (20, 20, 20) if style.light else (255, 255, 255)
    muted = (95, 95, 95) if style.light else (160, 160, 160)
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
