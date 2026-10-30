import dataclasses

import pytest

from features.community.models import DEFAULT_GOODBYE_TEXT, DEFAULT_WELCOME_TEXT
from features.community.services import greeting
from features.community.services.greeting import build_greeting
from features.community.services.guild_config import GuildConfig
from features.community.services.media import MediaStore
from tests.community.fakes import fake_member, png_bytes
from tests.community.test_media import gif_bytes

GIF = "https://media.tenor.com/abc/cat.gif"


def make_config(**overrides) -> GuildConfig:
    values = {
        field.name: None if field.type in ("int | None", "str | None") else False
        for field in dataclasses.fields(GuildConfig)
    }
    values.update(
        guild_id=999,
        welcome_text=DEFAULT_WELCOME_TEXT,
        goodbye_text=DEFAULT_GOODBYE_TEXT,
        welcome_card_enabled=True,
        goodbye_card_enabled=True,
    )
    values.update(overrides)
    return GuildConfig(**values)


@pytest.fixture
def store(db, tmp_path):
    return MediaStore(db, legacy_dir=tmp_path / "legacy")


async def test_default_card_is_png(store):
    message = await build_greeting(fake_member(), "welcome", make_config(), store, None)
    assert message.content == "Welcome <@111> to **Lost Island**! You're our 1,284th member."
    assert message.file is not None and message.file.filename == "welcome.png"
    assert set(message.send_kwargs()) == {"content", "file"}


async def test_gif_background_makes_animated_card(store):
    await store.save(999, "welcome", gif_bytes())
    message = await build_greeting(fake_member(), "welcome", make_config(welcome_gif_url=GIF), store, None)
    assert message.file.filename == "welcome.gif"


async def test_static_background_makes_png_card(store):
    await store.save(999, "welcome", png_bytes())
    message = await build_greeting(fake_member(), "welcome", make_config(welcome_gif_url="upload:bg.png"), store, None)
    assert message.file.filename == "welcome.png"


async def test_card_off_posts_background_on_its_own(store):
    await store.save(999, "welcome", gif_bytes())
    config = make_config(welcome_card_enabled=False, welcome_gif_url=GIF)
    message = await build_greeting(fake_member(), "welcome", config, store, None)
    assert message.file.filename == "welcome.gif"


async def test_card_off_and_no_background_sends_text_only(store):
    message = await build_greeting(fake_member(), "welcome", make_config(welcome_card_enabled=False), store, None)
    assert message.file is None
    assert message.send_kwargs() == {"content": message.content}


async def test_goodbye_uses_goodbye_settings(store):
    await store.save(999, "goodbye", gif_bytes())
    config = make_config(goodbye_gif_url=GIF)  # welcome has no background
    message = await build_greeting(fake_member(), "goodbye", config, store, None)
    assert message.content.startswith("Goodbye **subash**")
    assert message.file.filename == "goodbye.gif"


async def test_card_failure_still_sends_message(store, monkeypatch):
    def broken(*_args):
        raise RuntimeError("render failed")

    monkeypatch.setattr(greeting, "render_greeting_card", broken)
    message = await build_greeting(fake_member(), "welcome", make_config(), store, None)
    assert message.file is None
    assert message.content


async def test_avatar_download_failure_still_renders_card(store):
    member = fake_member()
    member.display_avatar._data = None  # read() raises DiscordException
    message = await build_greeting(member, "welcome", make_config(), store, None)
    assert message.file is not None


@pytest.mark.parametrize("kind", ["welcome", "goodbye"])
async def test_content_capped_at_discord_limit(store, kind):
    config = make_config(welcome_text="{user} " * 400, goodbye_text="{server} " * 400)
    message = await build_greeting(fake_member(), kind, config, store, None)
    assert len(message.content) <= 2000


# ---------------------------------------------------------------------- goodbye DM


class _Response:
    status = 403
    reason = "Forbidden"


async def test_goodbye_dm_sends_rendered_text(store):
    sent = []
    member = fake_member()

    async def send(content):
        sent.append(content)

    member.send = send
    config = make_config(goodbye_dm_text="Bye from {server}, {username}!")
    assert await greeting.send_goodbye_dm(member, config) is True
    assert sent == ["Bye from Lost Island, subash!"]


async def test_goodbye_dm_blocked_is_not_an_error(store):
    import discord

    member = fake_member()

    async def send(content):
        raise discord.Forbidden(_Response(), "Cannot send messages to this user")

    member.send = send
    assert await greeting.send_goodbye_dm(member, make_config(goodbye_dm_text="Bye")) is False


def test_greeting_settings_picks_the_right_prefix():
    config = make_config(
        welcome_enabled=True, welcome_channel_id=1, welcome_gif_url="w",
        goodbye_enabled=False, goodbye_channel_id=2, goodbye_gif_url="g", goodbye_card_enabled=False,
    )
    welcome = greeting.greeting_settings(config, "welcome")
    goodbye = greeting.greeting_settings(config, "goodbye")
    assert (welcome.enabled, welcome.channel_id, welcome.background_source, welcome.card_enabled) == (True, 1, "w", True)
    assert (goodbye.enabled, goodbye.channel_id, goodbye.background_source, goodbye.card_enabled) == (False, 2, "g", False)
    assert goodbye.text == DEFAULT_GOODBYE_TEXT
