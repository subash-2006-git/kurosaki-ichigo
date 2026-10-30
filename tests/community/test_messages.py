"""Welcome/goodbye/level-up messages as embeds: defaults, placeholders, link buttons."""

import pytest

from core.ui.embed_draft import DraftError
from features.community.services.greeting import build_greeting
from features.community.services.media import MediaStore
from features.community.services.messages import (
    DEFAULT_EMBEDS,
    buttons_text,
    fill,
    is_default,
    link_view,
    parse_buttons,
    stored_draft,
)
from tests.community.fakes import fake_member
from tests.community.test_greeting import make_config


def test_parse_buttons_with_and_without_emoji():
    buttons = parse_buttons("📜 Rules | https://example.com/rules\n\nWebsite | https://example.com ")
    assert buttons == [
        {"label": "Rules", "url": "https://example.com/rules", "emoji": "📜"},
        {"label": "Website", "url": "https://example.com", "emoji": ""},
    ]
    assert parse_buttons(buttons_text(buttons)) == buttons
    assert parse_buttons("") == []


@pytest.mark.parametrize("text", [
    "Rules https://example.com",                    # no separator
    "Rules | not-a-link",                          # bad link
    " | https://example.com",                      # no label
    "\n".join(f"B{i} | https://e.com/{i}" for i in range(6)),  # too many
])
def test_parse_buttons_rejects(text):
    with pytest.raises(DraftError):
        parse_buttons(text)


def test_stored_draft_defaults_and_saved():
    config = make_config(welcome_embed=None)
    assert is_default(config, "welcome")
    assert stored_draft(config, "welcome").title == DEFAULT_EMBEDS["welcome"]["title"]
    custom = make_config(welcome_embed={"title": "Hi {displayname}", "colour": 0x123456})
    assert not is_default(custom, "welcome")
    assert stored_draft(custom, "welcome").colour == 0x123456


def test_fill_uses_member_and_level():
    member = fake_member(display_name="Sam")
    draft = fill(stored_draft(make_config(), "levelup"), member, level=7)
    assert "Level 7" in draft.description and draft.description.startswith(member.mention)


def test_link_view():
    assert link_view([]) is None
    view = link_view([{"label": "Rules", "url": "https://e.com", "emoji": "📜"}])
    assert [(b.label, b.url) for b in view.children] == [("Rules", "https://e.com")]


@pytest.fixture
def store(db, tmp_path):
    return MediaStore(db, legacy_dir=tmp_path / "legacy")


async def test_welcome_embed_puts_card_inside_and_adds_buttons(store):
    config = make_config(
        welcome_use_embed=True,
        welcome_buttons=[{"label": "Rules", "url": "https://e.com/rules", "emoji": ""}],
    )
    branded = []
    message = await build_greeting(fake_member(display_name="Sam"), "welcome", config, store, None,
                                   brand=lambda e: branded.append(e) or e)
    assert message.embed is not None and branded == [message.embed]
    assert message.embed.title == "👋 Welcome to Lost Island!"
    assert message.embed.image.url == "attachment://welcome.png" and message.file.filename == "welcome.png"
    assert message.content == "<@111>"  # the default pings the new member above the embed
    assert [b.label for b in message.view.children] == ["Rules"]
    assert set(message.send_kwargs()) == {"content", "file", "embed", "view"}


async def test_custom_image_wins_over_card(store):
    config = make_config(welcome_use_embed=True, welcome_embed={"title": "Hi", "image_url": "https://e.com/banner.png"})
    message = await build_greeting(fake_member(), "welcome", config, store, None)
    assert message.embed.image.url == "https://e.com/banner.png"
    assert message.content is None and message.view is None
