import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from features.moderation.cogs.antiraid import AntiRaid
from features.moderation.case_buttons import HistoryButton, UndoButton, case_view, history_lines
from features.moderation.cogs.automod import AutoMod
from features.moderation.panel import _number
from features.moderation.utils.guild_cache import DEFAULT_AUTOMOD, GuildCache, _merge
from features.moderation.utils.modlog import appeal_view, case_embed
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


# ---- anti-raid join filter (greetings and auto-renames skip members it kicks) ----


class FakeConfigs:
    def __init__(self, raidmode=False, min_age=0):
        self.s = SimpleNamespace(raidmode_active=raidmode, raid={"min_account_age_days": min_age})

    async def get(self, guild_id):
        return self.s


def join(age_days=100, bot=False, can_kick=True):
    perms = SimpleNamespace(kick_members=can_kick)
    guild = SimpleNamespace(id=1, me=SimpleNamespace(guild_permissions=perms))
    created = discord.utils.utcnow() - timedelta(days=age_days)
    return SimpleNamespace(id=5, bot=bot, guild=guild, created_at=created)


def antiraid(**configs):
    cog = AntiRaid.__new__(AntiRaid)
    cog.bot = SimpleNamespace(mod_configs=FakeConfigs(**configs))
    cog._kicked = {}
    return cog


async def test_join_filter_raid_mode():
    assert await antiraid(raidmode=True).turns_away(join())
    assert not await antiraid(raidmode=False).turns_away(join())


async def test_join_filter_account_age():
    assert await antiraid(min_age=7).turns_away(join(age_days=2))
    assert not await antiraid(min_age=7).turns_away(join(age_days=30))


async def test_join_filter_ignores_bots_and_missing_kick_permission():
    assert not await antiraid(raidmode=True).turns_away(join(bot=True))
    assert not await antiraid(raidmode=True).turns_away(join(can_kick=False))


async def test_leave_filter_only_matches_members_antiraid_kicked():
    cog = antiraid(raidmode=True)
    member = join()
    # A real member leaving during raid mode still gets a goodbye.
    assert not await cog.turns_away(member, leaving=True)
    cog._remember_kick(member)
    assert await cog.turns_away(member, leaving=True)
    assert not await cog.turns_away(member, leaving=True)  # used up


# ---- restyled cases, case buttons, appeal button ----


def case(action="ban", num=7, active=True, duration=None, reason="Spamming"):
    return {"action": action, "case_num": num, "user_id": 42, "mod_id": 9, "reason": reason,
            "duration_seconds": duration, "active": active, "created_at": datetime(2026, 10, 30, tzinfo=timezone.utc)}


def test_case_embed_layout():
    embed = case_embed(case("tempban", duration=3600))
    assert embed.title == "⏱️ Temporary ban · Case #7"
    assert [f.name for f in embed.fields] == ["User", "Moderator", "Duration", "Reason"]
    assert embed.fields[3].value == ">>> Spamming" and embed.footer.text == "Case #7"
    assert case_embed(case("warn", active=False)).footer.text.endswith("removed")
    assert "Updated case #7" in case_embed(case(), title_prefix="Updated case").title


def test_case_view_offers_undo_only_when_possible():
    ids = lambda c, **k: [i.custom_id for i in case_view(c, **k).children]
    assert ids(case("ban")) == ["case:history:42", "case:undo:7"]
    assert ids(case("timeout")) == ["case:history:42", "case:undo:7"]
    assert ids(case("kick")) == ["case:history:42"]
    assert case_view(case("ban"), undone=True).children[1].item.disabled


def test_case_button_ids_parse():
    assert re.fullmatch(HistoryButton.__discord_ui_compiled_template__, "case:history:42")["user"] == "42"
    assert re.fullmatch(UndoButton.__discord_ui_compiled_template__, "case:undo:7")["num"] == "7"


def test_history_lines():
    line = history_lines([case("warn", active=False)])[0]
    assert line.startswith("`#7` ⚠️ **Warning** ~~removed~~") and "Spamming" in line


def test_appeal_view_only_for_real_links():
    assert appeal_view(None) is None and appeal_view("not a link") is None
    assert appeal_view("https://forms.gle/x").children[0].url == "https://forms.gle/x"


def test_panel_number_validation():
    assert _number(" 5 ", "X", 1, 10) == 5
    with pytest.raises(ValueError):
        _number("11", "X", 1, 10)
    with pytest.raises(ValueError):
        _number("five", "X", 1, 10)
