"""Card background images: download (from a URL or an uploaded file), check, and store.

The bot keeps its own copy because links expire (Discord attachment links stop working after
about a day) and so cards never depend on a third-party site being up when someone joins.
Copies live in the database, since hosts like Render's free tier wipe local files on restart.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import logging
import socket
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import aiohttp
from PIL import Image
from yarl import URL

from core.config import ROOT_DIR
from features.community.db import CommunityDB as Database
from features.community.repositories import MediaRepo

log = logging.getLogger(__name__)

Kind = Literal["welcome", "goodbye"]

# Where older versions kept backgrounds as files; only read to move them into the database.
LEGACY_MEDIA_DIR = ROOT_DIR / "data" / "media"
UPLOAD_PREFIX = "upload:"

MAX_URL_LENGTH = 500
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 4_000_000  # per frame, e.g. 2000x2000
MAX_FRAMES = 500
MAX_REDIRECTS = 3
TIMEOUT = aiohttp.ClientTimeout(total=15, sock_connect=5)
DIRECT_LINK_HINT = "Use a direct image link (right-click the GIF → Copy Image Address), usually ending in .gif."


class InvalidMedia(Exception):
    """The image can't be used; the message is safe to show to the admin."""


@dataclass(frozen=True)
class ImageInfo:
    format: str
    width: int
    height: int
    frames: int

    @property
    def animated(self) -> bool:
        return self.frames > 1

    def describe(self) -> str:
        kind = f"animated {self.format}, {self.frames} frames" if self.animated else self.format
        return f"{kind}, {self.width}×{self.height}"


# ---------------------------------------------------------------------- URL safety


def check_url_syntax(url: str) -> str:
    """Return the URL's hostname, or raise InvalidMedia."""
    if len(url) > MAX_URL_LENGTH:
        raise InvalidMedia(f"That link is too long (max {MAX_URL_LENGTH} characters).")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise InvalidMedia("The link must start with https://")
    if not parts.hostname:
        raise InvalidMedia("That doesn't look like a valid link.")
    if parts.username or parts.password:
        raise InvalidMedia("Links with a username or password aren't allowed.")
    return parts.hostname


def is_public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


async def _resolve(hostname: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise InvalidMedia("I couldn't find that website.") from None
    return [info[4][0] for info in infos]


async def _ensure_public(url: str) -> None:
    hostname = check_url_syntax(url)
    # Don't let the bot be used to probe its own host or private network.
    addresses = await _resolve(hostname)
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise InvalidMedia("That link points to a private address.")


# ---------------------------------------------------------------------- downloading


def _too_large(size: int) -> InvalidMedia:
    return InvalidMedia(f"That image is too large ({size / 1024 / 1024:.1f} MB, max 8 MB).")


async def _read_limited(response: aiohttp.ClientResponse) -> bytes:
    if response.content_length is not None and response.content_length > MAX_IMAGE_BYTES:
        raise _too_large(response.content_length)
    data = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        data.extend(chunk)
        if len(data) > MAX_IMAGE_BYTES:
            raise InvalidMedia("That image is too large (max 8 MB).")
    return bytes(data)


async def download_image(session: aiohttp.ClientSession, url: str) -> bytes:
    """Download an image from a public https URL (following up to 3 redirects)."""
    current = url.strip()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            await _ensure_public(current)
            # encoded=True sends the URL exactly as given (aiohttp would turn %28 into "(").
            async with session.get(URL(current, encoded=True), allow_redirects=False, timeout=TIMEOUT) as response:
                if 300 <= response.status < 400 and response.headers.get("Location"):
                    current = urljoin(current, response.headers["Location"])
                    continue
                if response.status in (401, 403):
                    raise InvalidMedia(
                        "That site doesn't let bots download its images. "
                        "Save the GIF and upload it with the `file` option instead."
                    )
                if response.status >= 400:
                    raise InvalidMedia(f"The link returned an error (HTTP {response.status}).")
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    raise InvalidMedia(f"That link is a web page, not an image. {DIRECT_LINK_HINT}")
                return await _read_limited(response)
    except asyncio.TimeoutError:
        raise InvalidMedia("The link took too long to respond.") from None
    except aiohttp.ClientError as exc:
        log.info("Image download failed for %s: %s", url, exc)
        raise InvalidMedia("I couldn't reach that link.") from None
    raise InvalidMedia(f"That link redirects too many times. {DIRECT_LINK_HINT}")


# ---------------------------------------------------------------------- checking


def inspect_image(data: bytes) -> ImageInfo:
    """Check the bytes are a usable image. CPU-bound: call through asyncio.to_thread."""
    if len(data) > MAX_IMAGE_BYTES:
        raise _too_large(len(data))
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise InvalidMedia(f"That image is too big ({width}×{height}). Keep it under about 2000×2000.")
            frames = getattr(image, "n_frames", 1)
            if frames > MAX_FRAMES:
                raise InvalidMedia(f"That GIF has too many frames ({frames}, max {MAX_FRAMES}).")
            image.seek(0)
            image.load()
            return ImageInfo(format=image.format or "image", width=width, height=height, frames=frames)
    except InvalidMedia:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        raise InvalidMedia("That file isn't an image I can read. Use a GIF, PNG, JPEG or WebP.") from None


# ---------------------------------------------------------------------- storage

CACHE_ENTRIES = 16


class MediaStore:
    """Background images kept in the database, with a small in-memory cache.

    Older versions stored them as files under data/media; a file found there is moved into the
    database the first time it's needed.
    """

    def __init__(self, db: Database, legacy_dir: Path = LEGACY_MEDIA_DIR) -> None:
        self.db = db
        self.legacy_dir = legacy_dir
        self._cache: OrderedDict[tuple[int, str], bytes] = OrderedDict()

    def _remember(self, key: tuple[int, str], data: bytes) -> None:
        self._cache[key] = data
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)

    async def load(self, guild_id: int, kind: Kind) -> bytes | None:
        key = (guild_id, kind)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        async with self.db.session() as session:
            data = await MediaRepo(session).get(guild_id, kind)
        if data is None:
            data = await self._migrate_legacy_file(guild_id, kind)
        if data is not None:
            self._remember(key, data)
        return data

    async def save(self, guild_id: int, kind: Kind, data: bytes) -> None:
        async with self.db.session() as session:
            await MediaRepo(session).put(guild_id, kind, data)
        self._remember((guild_id, kind), data)

    async def delete(self, guild_id: int, kind: Kind) -> None:
        async with self.db.session() as session:
            await MediaRepo(session).delete(guild_id, kind)
        self._cache.pop((guild_id, kind), None)
        await asyncio.to_thread(self._legacy_path(guild_id, kind).unlink, missing_ok=True)

    async def exists(self, guild_id: int, kind: Kind) -> bool:
        if (guild_id, kind) in self._cache:
            return True
        async with self.db.session() as session:
            return await MediaRepo(session).exists(guild_id, kind)

    def _legacy_path(self, guild_id: int, kind: Kind) -> Path:
        return self.legacy_dir / str(guild_id) / f"{kind}_background"

    async def _migrate_legacy_file(self, guild_id: int, kind: Kind) -> bytes | None:
        path = self._legacy_path(guild_id, kind)
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            return None
        await self.save(guild_id, kind, data)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        log.info("Moved %s background for guild %s from %s into the database", kind, guild_id, path)
        return data

    async def get_background(
        self, session: aiohttp.ClientSession | None, guild_id: int, kind: Kind, source: str | None
    ) -> bytes | None:
        """The stored background; if it's missing but a URL is configured, download and store it."""
        if not source:
            return None
        data = await self.load(guild_id, kind)
        if data is not None:
            return data
        if source.startswith(UPLOAD_PREFIX) or session is None:
            log.warning("%s background for guild %s is missing; set it again with /%s gif", kind, guild_id, kind)
            return None
        try:
            data = await download_image(session, source)
            await asyncio.to_thread(inspect_image, data)
        except InvalidMedia as exc:
            log.warning("Could not fetch %s background for guild %s: %s", kind, guild_id, exc)
            return None
        await self.save(guild_id, kind, data)
        log.info("Stored %s background for guild %s from its URL", kind, guild_id)
        return data
