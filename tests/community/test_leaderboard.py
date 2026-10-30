import io
from types import SimpleNamespace

import discord
import pytest
from PIL import Image

from features.community.cogs import leveling as leveling_cog
from features.community.cogs.leaderboard import LeaderboardView, display_name, leaderboard_lines, page_count
from features.community.cogs.leveling import accent_colour, rank_card
from features.community.models import XpSource
from features.community.services.cards import RANK_SIZE, render_rank_card
from features.community.services.leveling import LeaderboardEntry, LevelingService, RankInfo
from tests.community.fakes import FakeAsset, png_bytes

# ---------------------------------------------------------------------- rank card


def _png(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(level=27, xp_into_level=3_450, xp_for_next=5_500, position=2, total_xp=76_350),
        dict(level=0, xp_into_level=0, xp_for_next=100, position=None, total_xp=0),  # no XP yet
        dict(level=1, xp_into_level=1, xp_for_next=300, position=1, total_xp=101),  # sliver of progress
        dict(level=9_999, xp_into_level=10, xp_for_next=1_999_900, position=123_456, total_xp=999_800_010),
        dict(level=3, xp_into_level=50, xp_for_next=0, position=1, total_xp=900),  # defensive: no divide by zero
    ],
)
def test_rank_card_renders(kwargs):
    image = _png(
        render_rank_card(png_bytes(), "Ꭺɴɢᴇʟ 💓 " * 6, message_xp=1, voice_xp=2, accent=(230, 126, 34), **kwargs)
    )
    assert image.format == "PNG" and image.size == RANK_SIZE


def test_rank_card_without_avatar():
    data = render_rank_card(None, "Subash", 1, 0, 300, 1, 100, 100, 0)
    assert _png(data).size == RANK_SIZE


# ---------------------------------------------------------------------- leaderboard data


@pytest.fixture
def service(db):
    return LevelingService(db)


async def test_leaderboard_order_ties_and_paging(service):
    xp = {10: 900, 11: 2_500, 12: 900, 13: 100, 14: 50}
    for user_id, amount in xp.items():
        await service.award_xp(1, user_id, amount, XpSource.ADMIN, name=f"user{user_id}")
    await service.award_xp(2, 99, 1_000_000, XpSource.ADMIN)  # other guild

    first, total = await service.leaderboard(1, 0, 3)
    second, _ = await service.leaderboard(1, 1, 3)
    assert total == 5
    assert [(e.position, e.user_id) for e in first] == [(1, 11), (2, 10), (2, 12)]  # 10 and 12 tie
    assert [(e.position, e.user_id) for e in second] == [(4, 13), (5, 14)]
    assert (first[0].level, first[0].total_xp, first[0].name) == (5, 2_500, "user11")
    assert (await service.leaderboard(1, 5, 3))[0] == []


async def test_empty_leaderboard(service):
    assert await service.leaderboard(1, 0, 10) == ([], 0)


@pytest.mark.parametrize("total, pages", [(0, 1), (1, 1), (10, 1), (11, 2), (95, 10)])
def test_page_count(total, pages):
    assert page_count(total) == pages


def test_leaderboard_lines():
    entries = [
        LeaderboardEntry(1, 1, "a", 42, 85_420),
        LeaderboardEntry(2, 2, "b", 38, 71_230),
        LeaderboardEntry(2, 3, "c", 38, 71_230),
        LeaderboardEntry(4, 4, "d", 29, 50_000),
    ]
    lines = leaderboard_lines(entries, lambda e: e.name.upper())
    assert lines == [
        "🥇 **A** · Level 42 · 85,420 XP",
        "🥈 **B** · Level 38 · 71,230 XP",
        "🥈 **C** · Level 38 · 71,230 XP",
        "`#4` **D** · Level 29 · 50,000 XP",
    ]


def test_display_name_for_present_and_departed_members():
    members = {1: SimpleNamespace(display_name="**Boss**")}
    guild = SimpleNamespace(get_member=members.get)
    assert display_name(guild, LeaderboardEntry(1, 1, "old", 1, 1)) == r"\*\*Boss\*\*"
    assert display_name(guild, LeaderboardEntry(1, 2, "Gone_Guy", 1, 1)) == r"Gone\_Guy (left)"
    assert display_name(guild, LeaderboardEntry(1, 3, None, 1, 1)) == "User 3 (left)"


async def test_view_buttons_disable_at_the_ends():
    view = LeaderboardView(cog=None, owner_id=1, page=0, pages=3)
    assert view.previous.disabled and not view.next.disabled
    view.page = 2
    view._update_buttons()
    assert not view.previous.disabled and view.next.disabled


# ---------------------------------------------------------------------- /rank card helper


def fake_member(colour=0):
    return SimpleNamespace(
        id=5,
        display_name="Subash",
        colour=discord.Colour(colour),
        display_avatar=FakeAsset(png_bytes()),
        guild=SimpleNamespace(id=1),
    )


def test_accent_colour_uses_role_colour_or_blurple():
    assert accent_colour(fake_member(0xE67E22)) == (230, 126, 34)
    assert accent_colour(fake_member(0)) == discord.Colour.blurple().to_rgb()


async def test_rank_card_helper_builds_png_file():
    info = RankInfo(total_xp=650, message_xp=600, voice_xp=50, position=2, ranked_members=9)
    card = await rank_card(fake_member(), info)
    assert card.filename == "rank.png"
    assert _png(card.fp.read()).size == RANK_SIZE
    assert (await rank_card(fake_member(), None)).filename == "rank.png"


async def test_rank_card_failure_returns_none(monkeypatch):
    def broken(*_args):
        raise RuntimeError("render failed")

    monkeypatch.setattr(leveling_cog, "render_rank_card", broken)
    assert await rank_card(fake_member(), None) is None
