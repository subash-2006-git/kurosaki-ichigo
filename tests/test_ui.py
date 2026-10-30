"""Shared UI kit: EmbedDraft storage/templating, theme branding, and the control panel's building blocks."""

import re
from types import SimpleNamespace

import discord
import pytest

from core.panel.service import PanelEntry, PanelSection, PanelService
from core.ui import style
from core.ui.embed_draft import EmbedDraft, EmbedField
from core.ui.theme import Theme, ThemeStore

# ---- EmbedDraft -------------------------------------------------------------------


def sample_draft() -> EmbedDraft:
    return EmbedDraft(
        title="Welcome {user}", description="You are member {membercount}", colour=0x123456,
        thumbnail_url="{avatar}", footer_text="{server}", timestamp=True, message="Hi {user}!",
        fields=[EmbedField("Rules", "Read #rules, {user}", True)],
    )


def test_draft_round_trips_through_dict():
    draft = sample_draft()
    assert EmbedDraft.from_dict(draft.to_dict()) == draft


def test_from_dict_handles_empty_and_unknown_keys():
    assert EmbedDraft.from_dict(None) == EmbedDraft()
    assert EmbedDraft.from_dict({"title": "x", "added_in_future": 1}).title == "x"


def test_rendered_fills_every_text_and_leaves_original_alone():
    draft = sample_draft()
    filled = draft.rendered(lambda s: s.replace("{user}", "Sam").replace("{avatar}", "https://a/x.png")
                            .replace("{membercount}", "7").replace("{server}", "Island"))
    assert (filled.title, filled.description, filled.thumbnail_url, filled.footer_text, filled.message) == (
        "Welcome Sam", "You are member 7", "https://a/x.png", "Island", "Hi Sam!")
    assert filled.fields[0].value == "Read #rules, Sam" and filled.fields[0].inline
    assert draft.title == "Welcome {user}" and draft.fields[0].value == "Read #rules, {user}"


# ---- theme ------------------------------------------------------------------------


class FakePool:
    def __init__(self, rows=()):
        self.rows, self.writes = list(rows), []

    async def fetch(self, query):
        return self.rows

    async def execute(self, query, *args):
        self.writes.append(args)


def store_with(**themes) -> ThemeStore:
    store = ThemeStore(FakePool())
    store._themes = themes
    return store


def test_brand_colours_neutral_embeds_and_keeps_semantic_ones():
    store = store_with(**{"1": None})
    store._themes = {1: Theme(accent=0xF97316, footer_text="Island")}
    neutral = store.brand(style.info("Hi"), 1)
    assert neutral.colour.value == 0xF97316 and neutral.footer.text == "Island"
    legacy_blurple = store.brand(discord.Embed(colour=discord.Colour.blurple()), 1)
    assert legacy_blurple.colour.value == 0xF97316
    assert store.brand(style.fail("no"), 1).colour == style.ERROR
    assert store.brand(style.ok("yes"), 1).colour == style.SUCCESS


def test_brand_keeps_existing_footer_and_defaults_without_theme():
    store = store_with()
    store._themes = {1: Theme(accent=0x111111, footer_text="Island")}
    embed = discord.Embed().set_footer(text="Case #4")
    assert store.brand(embed, 1).footer.text == "Case #4"
    assert store.brand(discord.Embed(), 999).colour.value == style.DEFAULT_ACCENT
    assert store.brand(discord.Embed(), None).colour.value == style.DEFAULT_ACCENT


async def test_theme_update_writes_through_and_resets():
    pool = FakePool()
    store = ThemeStore(pool)
    theme = await store.update(5, accent=0xABCDEF, footer_text="  ")
    assert theme == Theme(0xABCDEF, None) and store.get(5) == theme
    assert pool.writes[-1] == (5, 0xABCDEF, None)
    await store.update(5, footer_text="Hello")
    assert store.get(5) == Theme(0xABCDEF, "Hello")  # accent untouched
    await store.update(5, accent=None)
    assert store.get(5).accent == style.DEFAULT_ACCENT


async def test_theme_load():
    store = ThemeStore(FakePool([{"guild_id": 1, "accent": None, "footer_text": "x"}, {"guild_id": 2, "accent": 5, "footer_text": None}]))
    await store.load()
    assert store.get(1) == Theme(style.DEFAULT_ACCENT, "x") and store.get(2) == Theme(5, None)


# ---- panel ------------------------------------------------------------------------


async def noop_status(bot, guild):
    return "fine"


async def noop_open(interaction):
    return None


def test_panel_entry_custom_id_round_trip():
    entry = PanelEntry("welcome")
    assert entry.item.custom_id == "panel:welcome"
    match = re.fullmatch(PanelEntry.__discord_ui_compiled_template__, "panel:welcome")
    assert match and match["key"] == "welcome"
    assert re.fullmatch(PanelEntry.__discord_ui_compiled_template__, "panel:Bad-Key") is None


def test_section_keys_are_validated():
    PanelSection("level_roles", "Levels", "📈", 1, noop_status, noop_open)
    with pytest.raises(ValueError):
        PanelSection("Bad Key", "x", "x", 1, noop_status, noop_open)


async def test_panel_lists_sections_in_order_with_status():
    async def broken(bot, guild):
        raise RuntimeError("db down")

    bot = SimpleNamespace(theme=store_with())
    service = PanelService(bot)
    service.register(PanelSection("zeta", "Zeta", "Z", 2, noop_status, noop_open))
    service.register(PanelSection("alpha", "Alpha", "A", 1, broken, noop_open))
    assert [s.key for s in service.sections] == ["alpha", "zeta"]

    guild = SimpleNamespace(id=1, name="Island")
    view = await service.build(guild)
    texts = [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]
    assert any("Alpha" in t and "Couldn't load" in t for t in texts)
    assert any("Zeta" in t and "fine" in t for t in texts)
    ids = [c.custom_id for c in view.walk_children() if isinstance(c, PanelEntry)]
    assert ids == ["panel:alpha", "panel:zeta"]
