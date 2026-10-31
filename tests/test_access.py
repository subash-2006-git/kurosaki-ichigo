"""OWNER_ONLY: commands, autocomplete, text commands and the panel are for the bot's owners only."""

from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

import core.config as config
from core.access import NOT_ALLOWED, is_allowed
from core.bot import Bot
from core.interactions import CoreTree
from core.panel.service import PanelEntry

OWNER, STRANGER = 1, 2


class FakeBot:
    async def is_owner(self, user):
        return user.id == OWNER


class Response:
    def __init__(self):
        self.sent, self.choices = [], None

    def is_done(self):
        return bool(self.sent)

    async def send_message(self, embed=None, ephemeral=False, **kwargs):
        self.sent.append((embed.description, ephemeral))

    async def autocomplete(self, choices):
        self.choices = choices


def interaction(user_id, kind=discord.InteractionType.application_command):
    return SimpleNamespace(client=FakeBot(), user=SimpleNamespace(id=user_id), type=kind, response=Response(),
                           command=None)


@pytest.fixture
def owner_only(monkeypatch):
    monkeypatch.setattr(config, "OWNER_ONLY", True)


async def test_is_allowed(owner_only, monkeypatch):
    assert await is_allowed(FakeBot(), SimpleNamespace(id=OWNER))
    assert not await is_allowed(FakeBot(), SimpleNamespace(id=STRANGER))
    monkeypatch.setattr(config, "OWNER_ONLY", False)
    assert await is_allowed(FakeBot(), SimpleNamespace(id=STRANGER))


async def test_slash_commands_refuse_others(owner_only):
    tree = CoreTree.__new__(CoreTree)
    mine = interaction(OWNER)
    assert await tree.interaction_check(mine) and mine.response.sent == []
    theirs = interaction(STRANGER)
    assert not await tree.interaction_check(theirs)
    assert theirs.response.sent == [(f"❌ {NOT_ALLOWED}", True)]
    typing = interaction(STRANGER, discord.InteractionType.autocomplete)
    assert not await tree.interaction_check(typing)
    assert typing.response.choices == [] and typing.response.sent == []


async def test_text_commands_refuse_others(owner_only):
    bot = Bot.__new__(Bot)
    bot.is_owner = FakeBot().is_owner
    assert await bot._owner_only(SimpleNamespace(author=SimpleNamespace(id=OWNER)))
    with pytest.raises(commands.NotOwner, match="owners"):
        await bot._owner_only(SimpleNamespace(author=SimpleNamespace(id=STRANGER)))


async def test_panel_buttons_refuse_others(owner_only):
    opened = []

    async def open_section(i):
        opened.append(i.user.id)

    panel = SimpleNamespace(section=lambda key: SimpleNamespace(open=open_section))
    for user_id in (OWNER, STRANGER):
        i = interaction(user_id)
        i.client.panel = panel
        await PanelEntry("welcome").callback(i)
    assert opened == [OWNER]
