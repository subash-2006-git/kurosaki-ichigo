import io

import pytest
from PIL import Image

from features.community.services import media
from features.community.services.media import (
    InvalidMedia,
    check_url_syntax,
    download_image,
    MediaStore,
    inspect_image,
    is_public_address,
)
from tests.community.fakes import png_bytes


def gif_bytes(frames=3, size=(40, 20)) -> bytes:
    images = [Image.new("RGB", size, (i * 60, 100, 200)) for i in range(frames)]
    output = io.BytesIO()
    images[0].save(output, format="GIF", save_all=True, append_images=images[1:], duration=50, loop=0)
    return output.getvalue()


# ---------------------------------------------------------------------- URL safety


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://example.com/a.gif", "https"),
        ("ftp://example.com/a.gif", "https"),
        ("https:///a.gif", "valid link"),
        ("https://user:pw@example.com/a.gif", "password"),
        ("https://example.com/" + "a" * 600, "too long"),
    ],
)
def test_rejects_bad_urls(url, message):
    with pytest.raises(InvalidMedia, match=message):
        check_url_syntax(url)


def test_accepts_https_url():
    assert check_url_syntax("https://media.tenor.com/abc/cat.gif") == "media.tenor.com"


@pytest.mark.parametrize(
    "address, public",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("192.168.1.10", False),
        ("172.16.0.1", False),
        ("169.254.169.254", False),  # cloud metadata endpoint
        ("::1", False),
        ("::ffff:127.0.0.1", False),
        ("fd00::1", False),
    ],
)
def test_public_address_detection(address, public):
    assert is_public_address(address) is public


# ---------------------------------------------------------------------- downloading


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size):
        for start in range(0, len(self.body), size):
            yield self.body[start : start + size]


class FakeResponse:
    def __init__(self, status, headers=None, body=b"", content_length=None):
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(body)
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.requested = []

    def get(self, url, **_kwargs):
        self.requested.append(str(url))
        return self.routes[str(url)]


@pytest.fixture
def public_dns(monkeypatch):
    hosts = {"cdn.example.com": "93.184.216.34", "evil.example.com": "10.0.0.1"}

    async def resolve(hostname):
        return [hosts.get(hostname, "93.184.216.34")]

    monkeypatch.setattr(media, "_resolve", resolve)


def image_response(body: bytes) -> FakeResponse:
    return FakeResponse(200, {"Content-Type": "image/gif"}, body=body)


async def test_private_hosts_rejected_before_any_request(monkeypatch):
    async def resolve(_hostname):
        return ["127.0.0.1"]

    monkeypatch.setattr(media, "_resolve", resolve)
    # session=None: the check must fail before the session is ever used.
    with pytest.raises(InvalidMedia, match="private"):
        await download_image(None, "https://localhost/a.gif")


async def test_downloads_image(public_dns):
    body = gif_bytes()
    session = FakeSession({"https://cdn.example.com/a.gif": image_response(body)})
    assert await download_image(session, "  https://cdn.example.com/a.gif ") == body


async def test_url_sent_without_re_encoding(public_dns):
    url = "https://cdn.example.com/Rotating_%28large%29.gif"
    session = FakeSession({url: image_response(b"x")})
    await download_image(session, url)
    assert session.requested == [url]


async def test_rejects_html(public_dns):
    page = FakeResponse(200, {"Content-Type": "text/html; charset=utf-8"}, body=b"<html>")
    with pytest.raises(InvalidMedia, match="web page"):
        await download_image(FakeSession({"https://cdn.example.com/p": page}), "https://cdn.example.com/p")


async def test_rejects_oversized_by_header_and_by_body(public_dns, monkeypatch):
    big = FakeResponse(200, {"Content-Type": "image/gif"}, content_length=20 * 1024 * 1024)
    with pytest.raises(InvalidMedia, match="too large"):
        await download_image(FakeSession({"https://cdn.example.com/b.gif": big}), "https://cdn.example.com/b.gif")

    # No Content-Length header: the limit is enforced while streaming.
    monkeypatch.setattr(media, "MAX_IMAGE_BYTES", 100)
    streamed = image_response(b"x" * 500)
    with pytest.raises(InvalidMedia, match="too large"):
        await download_image(FakeSession({"https://cdn.example.com/s.gif": streamed}), "https://cdn.example.com/s.gif")


@pytest.mark.parametrize("status, message", [(403, "upload it"), (404, "HTTP 404")])
async def test_http_errors(public_dns, status, message):
    session = FakeSession({"https://cdn.example.com/a.gif": FakeResponse(status)})
    with pytest.raises(InvalidMedia, match=message):
        await download_image(session, "https://cdn.example.com/a.gif")


async def test_follows_redirect(public_dns):
    body = gif_bytes()
    session = FakeSession(
        {
            "https://cdn.example.com/old.gif": FakeResponse(301, {"Location": "/new.gif"}),
            "https://cdn.example.com/new.gif": image_response(body),
        }
    )
    assert await download_image(session, "https://cdn.example.com/old.gif") == body


async def test_redirect_to_private_address_rejected(public_dns):
    session = FakeSession(
        {"https://cdn.example.com/a.gif": FakeResponse(302, {"Location": "https://evil.example.com/x"})}
    )
    with pytest.raises(InvalidMedia, match="private"):
        await download_image(session, "https://cdn.example.com/a.gif")
    assert "https://evil.example.com/x" not in session.requested


async def test_redirect_to_http_rejected(public_dns):
    session = FakeSession(
        {"https://cdn.example.com/a.gif": FakeResponse(301, {"Location": "http://cdn.example.com/a.gif"})}
    )
    with pytest.raises(InvalidMedia, match="https"):
        await download_image(session, "https://cdn.example.com/a.gif")


async def test_redirect_loop_rejected(public_dns):
    loop = FakeResponse(302, {"Location": "https://cdn.example.com/a.gif"})
    with pytest.raises(InvalidMedia, match="too many times"):
        await download_image(FakeSession({"https://cdn.example.com/a.gif": loop}), "https://cdn.example.com/a.gif")


# ---------------------------------------------------------------------- checking


def test_inspect_animated_gif():
    info = inspect_image(gif_bytes(frames=4))
    assert (info.format, info.width, info.height, info.frames, info.animated) == ("GIF", 40, 20, 4, True)
    assert info.describe() == "animated GIF, 4 frames, 40×20"


def test_inspect_static_png():
    info = inspect_image(png_bytes())
    assert not info.animated and info.describe() == "PNG, 64×64"


@pytest.mark.parametrize(
    "data, message",
    [(b"definitely not an image", "isn't an image"), (b"GIF89a" + b"\x00" * 20, "isn't an image")],
)
def test_inspect_rejects_non_images(data, message):
    with pytest.raises(InvalidMedia, match=message):
        inspect_image(data)


def test_inspect_rejects_huge_dimensions_and_frame_counts(monkeypatch):
    output = io.BytesIO()
    Image.new("RGB", (3000, 3000)).save(output, format="PNG")
    with pytest.raises(InvalidMedia, match="too big"):
        inspect_image(output.getvalue())

    monkeypatch.setattr(media, "MAX_FRAMES", 2)
    with pytest.raises(InvalidMedia, match="too many frames"):
        inspect_image(gif_bytes(frames=3))


# ---------------------------------------------------------------------- storage


@pytest.fixture
def store(db, tmp_path):
    return MediaStore(db, legacy_dir=tmp_path / "legacy")


async def test_save_load_delete(store):
    assert await store.load(1, "welcome") is None
    await store.save(1, "welcome", b"abc")
    await store.save(1, "goodbye", b"xyz")
    assert await store.load(1, "welcome") == b"abc"
    assert await store.load(1, "goodbye") == b"xyz"
    assert await store.load(2, "welcome") is None
    assert await store.exists(1, "welcome") and not await store.exists(2, "welcome")
    await store.delete(1, "welcome")
    await store.delete(1, "welcome")  # deleting twice is fine
    assert await store.load(1, "welcome") is None
    assert not await store.exists(1, "welcome")


async def test_save_replaces_and_survives_a_new_store(store, db, tmp_path):
    await store.save(1, "welcome", b"first")
    await store.save(1, "welcome", b"second")
    fresh = MediaStore(db, legacy_dir=tmp_path / "legacy")  # empty cache, as after a restart
    assert await fresh.load(1, "welcome") == b"second"


async def test_large_images_round_trip(store, db, tmp_path):
    data = bytes(range(256)) * (8 * 1024 * 1024 // 256)  # 8 MB, every byte value
    await store.save(1, "welcome", data)
    assert await MediaStore(db, legacy_dir=tmp_path / "legacy").load(1, "welcome") == data


async def test_cache_is_bounded(store, monkeypatch):
    monkeypatch.setattr(media, "CACHE_ENTRIES", 2)
    for guild_id in (1, 2, 3):
        await store.save(guild_id, "welcome", b"x")
    assert len(store._cache) == 2
    assert await store.load(1, "welcome") == b"x"  # evicted from cache, still in the database


async def test_legacy_file_moved_into_database(store, tmp_path):
    legacy = tmp_path / "legacy" / "1" / "welcome_background"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"old file")
    assert await store.load(1, "welcome") == b"old file"
    assert not legacy.exists()
    store._cache.clear()
    assert await store.load(1, "welcome") == b"old file"  # now served from the database


async def test_get_background_prefers_stored_copy(store):
    await store.save(1, "welcome", b"stored")
    # session=None proves no download is attempted.
    assert await store.get_background(None, 1, "welcome", "https://cdn.example.com/a.gif") == b"stored"


async def test_get_background_downloads_and_stores_missing_copy(store, public_dns):
    body = gif_bytes()
    session = FakeSession({"https://cdn.example.com/a.gif": image_response(body)})
    assert await store.get_background(session, 1, "welcome", "https://cdn.example.com/a.gif") == body
    store._cache.clear()
    assert await store.load(1, "welcome") == body


async def test_get_background_failures_return_none(store, public_dns):
    assert await store.get_background(None, 1, "welcome", None) is None
    assert await store.get_background(None, 1, "welcome", "upload:cat.gif") is None  # upload with no stored copy
    session = FakeSession({"https://cdn.example.com/a.gif": FakeResponse(404)})
    assert await store.get_background(session, 1, "welcome", "https://cdn.example.com/a.gif") is None
