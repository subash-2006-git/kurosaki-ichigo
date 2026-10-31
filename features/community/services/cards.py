"""Image cards rendered with Pillow.

Every function here is synchronous and CPU-bound: call it through asyncio.to_thread.
"""

from __future__ import annotations

import io
import logging
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, ImageOps

from core.config import ROOT_DIR

log = logging.getLogger(__name__)

ASSETS_DIR = ROOT_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"

GREETING_SIZE = (1024, 400)
AVATAR_SIZE = 170

# (top colour, bottom colour) for the generated background when no background.png exists.
GRADIENTS = {
    "welcome": ((88, 101, 242), (36, 24, 74)),
    "goodbye": ((74, 85, 104), (24, 26, 36)),
    "levels": ((44, 47, 66), (18, 19, 28)),
}

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont

# Tried in order for each character. Discord names often use small caps (ᴀʙᴄ), Cherokee look-alikes (ᎪᏴᏟ),
# math letters (𝓪𝓫𝓬), symbols (✓ ★), emoji, and decorative scripts such as Egyptian
# hieroglyphs (𓆩 𓆪), Javanese (꧁ ꧂) and Tibetan (༒), which no single font covers.
# bold.ttf / regular.ttf are optional overrides.
_FALLBACKS = (
    "NotoSansMath-Regular.ttf",
    "NotoSansSymbols2-Regular.ttf",
    "NotoSansSymbols-Regular.ttf",
    "NotoEmoji.ttf",
    "NotoSansEgyptianHieroglyphs-Regular.ttf",
    "NotoSansJavanese-Regular.ttf",
    "NotoSerifTibetan-Regular.ttf",
)
FONT_CHAINS = {
    "bold": ("bold.ttf", "NotoSans-Bold.ttf", "NotoSansCherokee-Bold.ttf", *_FALLBACKS),
    "regular": ("regular.ttf", "NotoSans-Regular.ttf", "NotoSansCherokee-Regular.ttf", *_FALLBACKS),
}
BOLD_FACES = {"bold.ttf", "NotoSans-Bold.ttf", "NotoSansCherokee-Bold.ttf"}


@dataclass(frozen=True)
class _Face:
    path: Path
    codepoints: frozenset[int]
    is_bold: bool


@dataclass(frozen=True)
class _Run:
    text: str
    font: Font
    faux_bold: bool


@lru_cache(maxsize=None)
def _faces(weight: str) -> tuple[_Face, ...]:
    faces = []
    for name in FONT_CHAINS[weight]:
        path = FONTS_DIR / name
        if not path.is_file():
            continue
        try:
            with TTFont(path, lazy=True) as font:
                codepoints = frozenset(font.getBestCmap() or {})
        except Exception:
            log.warning("Could not read font %s, skipping it", path)
            continue
        faces.append(_Face(path, codepoints, name in BOLD_FACES))
    if not faces:
        log.warning("No fonts found in %s; using Pillow's built-in font (Latin only)", FONTS_DIR)
    return tuple(faces)


@lru_cache(maxsize=128)
def _truetype(path: Path, size: int) -> Font:
    return ImageFont.truetype(str(path), size)


# CJK punctuation that decorates names, swapped for look-alikes the bundled fonts have
# (a Japanese font big enough to cover it would be 5+ MB).
LOOKALIKES = {
    "・": "·",  # katakana middle dot
    "･": "·",  # halfwidth katakana middle dot
    "、": ",",
    "。": ".",
    "【": "[",
    "】": "]",
    "「": "“",
    "」": "”",
    "『": "‘",
    "』": "’",
    "〜": "~",
    "～": "~",
}

_reported_missing: set[int] = set()


def _report_missing(char: str) -> None:
    """Log each character no font can draw, once, so gaps are easy to spot and fix."""
    if ord(char) not in _reported_missing:
        _reported_missing.add(ord(char))
        log.warning(
            "No bundled font has U+%04X %s; it renders as a box (add a font for it in assets/fonts)",
            ord(char),
            unicodedata.name(char, "unknown character"),
        )


def _is_invisible(char: str) -> bool:
    """Joiners, variation selectors and combining marks: dropped if no font has them."""
    return unicodedata.category(char) in {"Cf", "Mn", "Me"} or 0xFE00 <= ord(char) <= 0xFE0F


def _runs(text: str, weight: str, size: int) -> list[_Run]:
    """Split text into runs, each drawn with the first font that has its characters."""
    faces = _faces(weight)
    if not faces:
        return [_Run(text, ImageFont.load_default(size=size), faux_bold=weight == "bold")]

    def face_for(char: str) -> _Face | None:
        return next((f for f in faces if ord(char) in f.codepoints), None)

    groups: list[tuple[_Face, list[str]]] = []

    def add(face: _Face, char: str) -> None:
        if groups and groups[-1][0] is face:
            groups[-1][1].append(char)
        else:
            groups.append((face, [char]))

    for char in text:
        face = face_for(char)
        if face is not None:
            add(face, char)
            continue
        if _is_invisible(char):
            continue
        # No font has it: try a look-alike, then its plain compatibility form (fullwidth Ｆ -> F).
        for plain in (LOOKALIKES.get(char), unicodedata.normalize("NFKC", char)):
            if plain and plain != char and all(face_for(c) for c in plain):
                for c in plain:
                    add(face_for(c), c)  # type: ignore[arg-type]
                break
        else:
            _report_missing(char)
            add(faces[0], char)  # draws the font's "missing glyph" box
    return [
        _Run("".join(chars), _truetype(face.path, size), faux_bold=weight == "bold" and not face.is_bold)
        for face, chars in groups
    ]


def _width(runs: list[_Run]) -> float:
    return sum(run.font.getlength(run.text) for run in runs)


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    column = Image.new("RGB", (1, 256))
    for y in range(256):
        t = y / 255
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)))
    return column.resize(size, Image.Resampling.BICUBIC).convert("RGBA")


def _background(kind: str, size: tuple[int, int]) -> Image.Image:
    custom = ASSETS_DIR / kind / "background.png"
    if custom.is_file():
        try:
            with Image.open(custom) as image:
                return ImageOps.fit(image.convert("RGBA"), size, Image.Resampling.LANCZOS)
        except OSError:
            log.warning("Could not load %s, using the default background", custom)
    top, bottom = GRADIENTS.get(kind, GRADIENTS["welcome"])
    return _gradient(size, top, bottom)


def _circle_avatar(avatar_png: bytes | None, size: int) -> Image.Image:
    scale = 4  # draw the mask large and shrink it for smooth edges
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
    mask = mask.resize((size, size), Image.Resampling.LANCZOS)

    avatar: Image.Image | None = None
    if avatar_png:
        try:
            with Image.open(io.BytesIO(avatar_png)) as image:
                avatar = ImageOps.fit(image.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
        except OSError:
            log.warning("Could not decode avatar image, using a placeholder")
    if avatar is None:
        avatar = Image.new("RGBA", (size, size), (114, 118, 125, 255))

    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(avatar, (0, 0), mask)
    return result


def _fit_text(text: str, weight: str, size: int, min_size: int, max_width: int) -> tuple[list[_Run], int]:
    """Shrink the font until the text fits, then trim with an ellipsis if it still doesn't."""
    while size > min_size and _width(_runs(text, weight, size)) > max_width:
        size -= 2
    runs = _runs(text, weight, size)
    if _width(runs) <= max_width:
        return runs, size
    while text and _width(_runs(text + "…", weight, size)) > max_width:
        text = text[:-1]
    return _runs(text.rstrip() + "…", weight, size), size


def _draw_centered(layer: Image.Image, runs: list[_Run], size: int, center_x: int, top: int, fill) -> None:
    _draw_text(layer, runs, size, center_x, top, fill, align="center")


def _draw_text(
    layer: Image.Image, runs: list[_Run], size: int, x: float, top: int, fill, *, align: str = "left"
) -> None:
    """Draw a text line onto an RGBA layer, blending correctly over semi-transparent pixels.

    x is the left edge, centre or right edge depending on align.
    """
    # Text is drawn as a coverage mask, then composited as a solid colour through it. Drawing
    # straight onto a transparent layer would darken the anti-aliased edges.
    mask = Image.new("L", layer.size, 0)
    draw = ImageDraw.Draw(mask)
    # All runs share one baseline, placed where the primary font's ascender would put it.
    primary = runs[0].font if runs else ImageFont.load_default(size=size)
    ascent = primary.getmetrics()[0] if isinstance(primary, ImageFont.FreeTypeFont) else size
    baseline = top + ascent
    width = _width(runs)
    x = {"left": x, "center": x - width / 2, "right": x - width}[align]
    for run in runs:
        # Fallback fonts have no bold weight; a thin stroke stands in for it.
        stroke = 1 if run.faux_bold else 0
        draw.text((x, baseline), run.text, font=run.font, fill=255, anchor="ls", stroke_width=stroke, stroke_fill=255)
        x += run.font.getlength(run.text)
    colour = Image.new("RGBA", layer.size, (*fill, 255))
    colour.putalpha(mask)
    layer.alpha_composite(colour)


def _greeting_overlay(avatar_png: bytes | None, title: str, name: str, subtitle: str) -> Image.Image:
    """Everything except the background, on a transparent 1024x400 layer."""
    width, height = GREETING_SIZE
    layer = Image.new("RGBA", GREETING_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # Darken the middle so text stays readable on any background.
    draw.rounded_rectangle((24, 20, width - 24, height - 20), radius=28, fill=(0, 0, 0, 110))

    center_x = width // 2
    avatar_top = 34
    ring = 6
    draw.ellipse(
        (
            center_x - AVATAR_SIZE // 2 - ring,
            avatar_top - ring,
            center_x + AVATAR_SIZE // 2 + ring,
            avatar_top + AVATAR_SIZE + ring,
        ),
        fill=(255, 255, 255, 255),
    )
    layer.alpha_composite(_circle_avatar(avatar_png, AVATAR_SIZE), (center_x - AVATAR_SIZE // 2, avatar_top))

    max_width = width - 120
    title_runs, title_size = _fit_text(title.upper(), "bold", 54, 30, max_width)
    _draw_centered(layer, title_runs, title_size, center_x, 214, (255, 255, 255))

    name_runs, name_size = _fit_text(name, "bold", 38, 22, max_width)
    _draw_centered(layer, name_runs, name_size, center_x, 280, (235, 236, 255))

    subtitle_runs, subtitle_size = _fit_text(subtitle, "regular", 26, 18, max_width)
    _draw_centered(layer, subtitle_runs, subtitle_size, center_x, 330, (190, 194, 214))
    return layer


@dataclass(frozen=True)
class RenderedCard:
    data: bytes
    extension: str  # "png" or "gif"


def _sample_frames(image: Image.Image, size: tuple[int, int], max_frames: int) -> list[tuple[Image.Image, int]]:
    """Up to max_frames evenly spaced frames, each resized to size, with merged durations."""
    total = getattr(image, "n_frames", 1)
    step = max(1, -(-total // max_frames))  # ceiling division
    frames: list[tuple[Image.Image, int]] = []
    for index in range(total):
        image.seek(index)
        duration = int(image.info.get("duration") or 100)
        if index % step == 0:
            frame = ImageOps.fit(image.convert("RGBA"), size, Image.Resampling.BILINEAR)
            frames.append((frame, duration))
        else:
            previous, previous_duration = frames[-1]
            frames[-1] = (previous, previous_duration + duration)
    # Browsers treat delays under 20 ms as 100 ms, which would make the animation slow down.
    return [(frame, max(20, duration)) for frame, duration in frames]


def _shared_palette(frames: list[Image.Image]) -> Image.Image:
    """One 256-colour palette built from up to 8 sample frames stacked into a strip."""
    samples = frames[:: max(1, len(frames) // 8)][:8]
    width, height = samples[0].size
    strip = Image.new("RGB", (width, height * len(samples)))
    for index, frame in enumerate(samples):
        strip.paste(frame, (0, index * height))
    return strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def _encode_animated(source: Image.Image, overlay: Image.Image, size: tuple[int, int], max_frames: int) -> bytes:
    layer = overlay if overlay.size == size else overlay.resize(size, Image.Resampling.LANCZOS)
    sampled = _sample_frames(source, size, max_frames)
    frames = [Image.alpha_composite(frame, layer).convert("RGB") for frame, _ in sampled]
    # A shared palette without dithering is ~5x faster than per-frame palettes, smaller,
    # and avoids dither noise shimmering between frames.
    palette = _shared_palette(frames)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    output = io.BytesIO()
    quantized[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=quantized[1:],
        duration=[duration for _, duration in sampled],
        loop=0,
        disposal=1,
    )
    return output.getvalue()


# (scale of 1024x400, max frames), tried in order until the GIF fits under MAX_ANIMATED_BYTES.
ANIMATED_ATTEMPTS = ((0.75, 60), (0.625, 50), (0.5, 40), (0.4, 30))
MAX_ANIMATED_BYTES = 8 * 1024 * 1024


def render_greeting_card(
    kind: str,
    avatar_png: bytes | None,
    title: str,
    name: str,
    subtitle: str,
    background: bytes | None = None,
) -> RenderedCard:
    """A 1024x400 card: avatar, title (WELCOME/GOODBYE), member name and a subtitle line.

    background is an optional image file. An animated GIF produces an animated GIF card
    (smaller, with fewer frames, so it stays under Discord's upload limit).
    """
    overlay = _greeting_overlay(avatar_png, title, name, subtitle)

    source: Image.Image | None = None
    if background is not None:
        try:
            source = Image.open(io.BytesIO(background))
            source.load()
        except OSError:
            log.warning("Could not decode the background image, using the default")
            source = None

    if source is not None and getattr(source, "is_animated", False):
        with source:
            data = b""
            for scale, max_frames in ANIMATED_ATTEMPTS:
                size = (round(GREETING_SIZE[0] * scale), round(GREETING_SIZE[1] * scale))
                data = _encode_animated(source, overlay, size, max_frames)
                if len(data) <= MAX_ANIMATED_BYTES:
                    return RenderedCard(data, "gif")
            log.warning("Animated card is still %d bytes at the smallest size; sending it anyway", len(data))
            return RenderedCard(data, "gif")

    if source is not None:
        with source:
            base = ImageOps.fit(source.convert("RGBA"), GREETING_SIZE, Image.Resampling.LANCZOS)
    else:
        base = _background(kind, GREETING_SIZE)

    output = io.BytesIO()
    Image.alpha_composite(base, overlay).convert("RGB").save(output, format="PNG", optimize=True)
    return RenderedCard(output.getvalue(), "png")


# ---------------------------------------------------------------------- rank card

RANK_SIZE = (934, 282)
RANK_AVATAR = 180
TEXT_MAIN = (255, 255, 255)
TEXT_MUTED = (185, 187, 196)
BAR_TRACK = (72, 75, 84, 255)


def _rounded_bar(size: tuple[int, int], fill: tuple[int, int, int, int]) -> Image.Image:
    """An anti-aliased pill shape (drawn 4x larger and scaled down)."""
    scale = 4
    width, height = size
    big = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, width * scale - 1, height * scale - 1), radius=height * scale // 2, fill=fill
    )
    return big.resize(size, Image.Resampling.LANCZOS)


def render_rank_card(
    avatar_png: bytes | None,
    name: str,
    level: int,
    xp_into_level: int,
    xp_for_next: int,
    position: int | None,
    total_xp: int,
    message_xp: int,
    voice_xp: int,
    accent: tuple[int, int, int] = (88, 101, 242),
) -> bytes:
    """A 934x282 PNG: avatar, name, level, rank, progress bar and XP breakdown.

    position is None for a member with no XP yet.
    """
    width, height = RANK_SIZE
    card = _background("levels", RANK_SIZE)
    layer = Image.new("RGBA", RANK_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((16, 16, width - 16, height - 16), radius=24, fill=(0, 0, 0, 120))

    # Avatar with an accent-coloured ring.
    avatar_x, avatar_y, ring = 44, (height - RANK_AVATAR) // 2, 5
    ring_box = (avatar_x - ring, avatar_y - ring, avatar_x + RANK_AVATAR + ring, avatar_y + RANK_AVATAR + ring)
    layer.alpha_composite(_rounded_bar((ring_box[2] - ring_box[0], ring_box[3] - ring_box[1]), (*accent, 255)), ring_box[:2])
    layer.alpha_composite(_circle_avatar(avatar_png, RANK_AVATAR), (avatar_x, avatar_y))

    left, right = 262, width - 48
    content_width = right - left

    # Top-right: RANK #n   LEVEL n (number big, label small), laid out right to left.
    x = right
    for label, value in (("LEVEL", f"{level:,}"), ("RANK", f"#{position:,}" if position else "–")):
        value_runs, value_size = _fit_text(value, "bold", 48, 30, 200)
        _draw_text(layer, value_runs, value_size, x, 40, accent if label == "LEVEL" else TEXT_MAIN, align="right")
        x -= _width(value_runs) + 8
        label_runs, label_size = _fit_text(label, "regular", 20, 16, 100)
        _draw_text(layer, label_runs, label_size, x, 64, TEXT_MUTED, align="right")
        x -= _width(label_runs) + 28
    stats_left = x

    # Name, under the stats if they leave too little room beside them.
    name_room = stats_left - left - 16
    name_top = 52
    if name_room < 220:
        name_room, name_top = content_width, 106
    name_runs, name_size = _fit_text(name, "bold", 40, 22, name_room)
    _draw_text(layer, name_runs, name_size, left, name_top, TEXT_MAIN)

    # Progress bar with "into / needed XP" above its right end.
    bar_top, bar_height = 172, 34
    xp_runs, xp_size = _fit_text(f"{xp_into_level:,} / {xp_for_next:,} XP", "regular", 22, 16, content_width)
    _draw_text(layer, xp_runs, xp_size, right, bar_top - 34, TEXT_MUTED, align="right")
    layer.alpha_composite(_rounded_bar((content_width, bar_height), BAR_TRACK), (left, bar_top))
    fraction = 0 if xp_for_next <= 0 else min(1.0, max(0.0, xp_into_level / xp_for_next))
    filled = round(content_width * fraction)
    if filled > 0:
        # Never narrower than the bar is tall, so a sliver still reads as a rounded pill.
        layer.alpha_composite(_rounded_bar((max(filled, bar_height), bar_height), (*accent, 255)), (left, bar_top))

    breakdown = f"Total {total_xp:,} XP  ·  Messages {message_xp:,}  ·  Voice {voice_xp:,}"
    breakdown_runs, breakdown_size = _fit_text(breakdown, "regular", 20, 14, content_width)
    _draw_text(layer, breakdown_runs, breakdown_size, left, bar_top + bar_height + 16, TEXT_MUTED)

    output = io.BytesIO()
    Image.alpha_composite(card, layer).convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue()
