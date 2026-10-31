"""OWNER_ONLY: per server, the owner and the members they allow; the bot's operators everywhere."""

from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

import core.config as config
from core.access import NOT_ALLOWED, AccessStore, can_manage, is_allowed
from core.bot import Bot
from core.interactions import CoreTree
from core.panel.access import AccessEditor
from core.panel.service import PanelEntry

OPERATOR, SERVER_OWNER, ALLOWED, STRANGER = 1, 2, 3, 4
GUILD = SimpleNamespace(id=10, owner_id=SERVER_OWNER)
OTHER_GUILD = SimpleNamespace(id=11, owner_id=99)


class FakeAccess:
    async def members(self, guild_id):
        return frozenset({ALLOWED}) if guild_id == GUILD.id else frozenset()


class FakeBot:
    access = FakeAccess()

    async def is_owner(self, user):
        return user.id == OPERATOR


def user(id_):
    return SimpleNamespace(id=id_)


@pytest.fixture(autouse=True)
def owner_only(monkeypatch):
    monkeypatch.setattr(config, "OWNER_ONLY", True)


async def test_who_is_allowed_where(monkeypatch):
    bot = FakeBot()
    for who in (OPERATOR, SERVER_OWNER, ALLOWED):
        assert await is_allowed(bot, user(who), GUILD)
    assert not await is_allowed(bot, user(STRANGER), GUILD)
    # each server has its own list and owner; operators work everywhere
    assert not await is_allowed(bot, user(ALLOWED), OTHER_GUILD)
    assert not await is_allowed(bot, user(SERVER_OWNER), OTHER_GUILD)
    assert await is_allowed(bot, user(OPERATOR), OTHER_GUILD)
    # DMs: operators only
    assert await is_allowed(bot, user(OPERATOR), None) and not await is_allowed(bot, user(ALLOWED), None)
    monkeypatch.setattr(config, "OWNER_ONLY", False)
    assert await is_allowed(bot, user(STRANGER), GUILD)


async def test_only_the_server_owner_and_operators_manage_the_list():
    bot = FakeBot()
    assert await can_manage(bot, user(SERVER_OWNER), GUILD) and await can_manage(bot, user(OPERATOR), GUILD)
    assert not await can_manage(bot, user(ALLOWED), GUILD)


async def test_store(core_db):
    store = AccessStore(core_db.pool)
    assert await store.members(1) == frozenset()
    await store.set_members(1, [5, 6])
    assert await store.members(1) == {5, 6} and await store.members(2) == frozenset()
    with pytest.raises(ValueError):
        await store.set_members(1, list(range(26)))


class Response:
    def __init__(self):
        self.sent, self.choices = [], None

    def is_done(self):
        return bool(self.sent)

    async def send_message(self, embed=None, ephemeral=False, **kwargs):
        self.sent.append((embed.description, ephemeral))

    async def autocomplete(self, choices):
        self.choices = choices


def interaction(user_id, kind=discord.InteractionType.application_command, guild=GUILD):
    return SimpleNamespace(client=FakeBot(), user=user(user_id), guild=guild, type=kind, response=Response(),
                           command=None)


async def test_slash_commands_refuse_others():
    tree = CoreTree.__new__(CoreTree)
    mine = interaction(ALLOWED)
    assert await tree.interaction_check(mine) and mine.response.sent == []
    theirs = interaction(STRANGER)
    assert not await tree.interaction_check(theirs)
    assert theirs.response.sent == [(f"❌ {NOT_ALLOWED}", True)]
    typing = interaction(STRANGER, discord.InteractionType.autocomplete)
    assert not await tree.interaction_check(typing)
    assert typing.response.choices == [] and typing.response.sent == []


async def test_text_commands_refuse_others():
    bot = Bot.__new__(Bot)
    bot.is_owner, bot.access = FakeBot().is_owner, FakeAccess()
    assert await bot._owner_only(SimpleNamespace(author=user(SERVER_OWNER), guild=GUILD))
    with pytest.raises(commands.NotOwner, match="server owner"):
        await bot._owner_only(SimpleNamespace(author=user(STRANGER), guild=GUILD))


async def test_panel_buttons_refuse_others():
    opened = []

    async def open_section(i):
        opened.append(i.user.id)

    panel = SimpleNamespace(section=lambda key: SimpleNamespace(open=open_section))
    for user_id in (ALLOWED, STRANGER):
        i = interaction(user_id)
        i.client.panel = panel
        await PanelEntry("welcome").callback(i)
    assert opened == [ALLOWED]


async def test_access_editor_shows_the_picker_only_to_those_who_manage(core_db):
    bot = FakeBot()
    bot.access = AccessStore(core_db.pool)
    bot.theme = SimpleNamespace(get=lambda guild: SimpleNamespace(colour=1))
    await bot.access.set_members(GUILD.id, [ALLOWED])
    for who, has_picker in ((SERVER_OWNER, True), (ALLOWED, False)):
        editor = AccessEditor(bot, GUILD, who)
        await editor.build()
        pickers = [c for c in editor.walk_children() if isinstance(c, discord.ui.UserSelect)]
        assert bool(pickers) == has_picker
        if pickers:
            assert [d.id for d in pickers[0].default_values] == [ALLOWED]
