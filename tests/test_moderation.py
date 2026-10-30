from datetime import timedelta
from types import SimpleNamespace

import pytest

from features.moderation.cogs.automod import AutoMod
from features.moderation.utils.guild_cache import DEFAULT_AUTOMOD, GuildCache, _merge
from features.moderation.utils.time import humanize, parse_duration


@pytest.mark.parametrize(
    ("text", "expected"),
    [("10m", timedelta(minutes=10)), ("1h30m", timedelta(hours=1, minutes=30)), ("7d", timedelta(days=7)),
     ("2W", timedelta(weeks=2)), ("1h 5s", timedelta(hours=1, seconds=5))],
)
def test_parse_duration_valid(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "10x", "10", "0m", "5m garbage"])
def test_parse_duration_invalid(text):
    assert parse_duration(text) is None


def test_humanize():
    assert humanize(None) == "permanent"
    assert humanize(90) == "1 minute, 30 seconds"
    assert humanize(694_861) == "1 week, 1 day"


def test_merge_keeps_new_default_keys():
    merged = _merge(DEFAULT_AUTOMOD, {"spam": {"messages": 9}, "links": True})
    assert merged["spam"] == {"enabled": True, "messages": 9, "seconds": 5}
    assert merged["links"] is True
    assert merged["invites"] is True
    assert DEFAULT_AUTOMOD["spam"]["messages"] == 5  # defaults not mutated


class FakeModDB:
    def __init__(self, words=(), whitelist=()):
        self.words, self.whitelist = list(words), list(whitelist)

    async def get_guild_config(self, guild_id):
        return None

    async def get_banned_words(self, guild_id):
        return self.words

    async def get_whitelist(self, guild_id):
        return self.whitelist


def message(content, mentions=()):
    return SimpleNamespace(content=content, raw_mentions=list(mentions), raw_role_mentions=[])


async def settings(words=(), whitelist=(), **automod):
    s = await GuildCache(FakeModDB(words, whitelist)).get(1)
    s.automod.update(automod)
    return s


async def test_banned_word_matches_whole_words_only():
    automod, s = AutoMod.__new__(AutoMod), await settings(words=["bad"])
    assert automod.check_content(message("that is BAD!"), s) == "Use of a banned word"
    assert automod.check_content(message("badminton"), s) is None


async def test_invites_and_links():
    automod, s = AutoMod.__new__(AutoMod), await settings(whitelist=["youtube.com"], links=True)
    assert automod.check_content(message("join discord.gg/abc"), s) == "Posting server invites"
    assert automod.check_content(message("https://www.youtube.com/watch?v=1"), s) is None
    assert automod.check_content(message("https://music.youtube.com/x"), s) is None
    assert automod.check_content(message("https://evil.example/x"), s) == "Posting links that aren't whitelisted"


async def test_mass_mentions():
    automod, s = AutoMod.__new__(AutoMod), await settings()
    assert automod.check_content(message("hi", mentions=range(6)), s) == "Mass mentions (6)"
    assert automod.check_content(message("hi", mentions=[1, 1, 1, 1, 1, 1]), s) is None
