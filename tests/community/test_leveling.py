import asyncio
from types import SimpleNamespace

import pytest

from features.community.cogs.leveling import earns_message_xp, progress_bar, rank_embed
from features.community.models import XpSource
from features.community.services.leveling import LevelingService, level_for_xp, progress, xp_for_level
from features.community.utils.cooldowns import Cooldowns

# ---------------------------------------------------------------------- formula


@pytest.mark.parametrize(
    "xp, level",
    [(0, 0), (99, 0), (100, 1), (399, 1), (400, 2), (899, 2), (900, 3), (9_999, 9), (10_000, 10), (-50, 0)],
)
def test_level_for_xp(xp, level):
    assert level_for_xp(xp) == level


@pytest.mark.parametrize("level", range(0, 200))
def test_formula_round_trips(level):
    assert level_for_xp(xp_for_level(level)) == level
    if level:
        assert level_for_xp(xp_for_level(level) - 1) == level - 1


def test_progress():
    assert progress(0) == progress(0).__class__(level=0, xp_into_level=0, xp_for_next=100)
    p = progress(650)  # level 2 starts at 400, level 3 at 900
    assert (p.level, p.xp_into_level, p.xp_for_next) == (2, 250, 500)
    assert p.fraction == 0.5


@pytest.mark.parametrize("fraction, bar", [(0, "▱" * 12), (0.5, "▰" * 6 + "▱" * 6), (1, "▰" * 12), (1.7, "▰" * 12)])
def test_progress_bar(fraction, bar):
    assert progress_bar(fraction) == bar


# ---------------------------------------------------------------------- cooldowns


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_cooldown_blocks_until_expired():
    clock = FakeClock()
    cooldowns = Cooldowns(clock)
    assert cooldowns.try_acquire("a", 30)
    clock.now += 29.9
    assert not cooldowns.try_acquire("a", 30)
    assert cooldowns.try_acquire("b", 30)  # other keys are independent
    clock.now += 0.1
    assert cooldowns.try_acquire("a", 30)


def test_blocked_attempts_do_not_extend_the_cooldown():
    clock = FakeClock()
    cooldowns = Cooldowns(clock)
    assert cooldowns.try_acquire("a", 30)
    for _ in range(5):
        clock.now += 5
        assert not cooldowns.try_acquire("a", 30)
    clock.now += 5  # 30s after the first award
    assert cooldowns.try_acquire("a", 30)


def test_zero_cooldown_always_allows():
    cooldowns = Cooldowns(FakeClock())
    assert all(cooldowns.try_acquire("a", 0) for _ in range(3))


def test_old_entries_are_pruned(monkeypatch):
    from features.community.utils import cooldowns as module

    monkeypatch.setattr(module, "PRUNE_AT", 3)
    clock = FakeClock()
    cooldowns = Cooldowns(clock)
    for key in "abc":
        cooldowns.try_acquire(key, 30)
    clock.now += module.FORGET_AFTER + 1
    cooldowns.try_acquire("d", 30)  # 4 entries > PRUNE_AT: a, b, c are old and dropped
    assert len(cooldowns) == 1


# ---------------------------------------------------------------------- message filter


def fake_message(*, guild=True, bot=False, webhook_id=None, system=False):
    return SimpleNamespace(
        guild=object() if guild else None,
        author=SimpleNamespace(bot=bot),
        webhook_id=webhook_id,
        is_system=lambda: system,
    )


@pytest.mark.parametrize(
    "kwargs, earns",
    [
        ({}, True),
        ({"guild": False}, False),
        ({"bot": True}, False),
        ({"webhook_id": 123}, False),
        ({"system": True}, False),
    ],
)
def test_earns_message_xp(kwargs, earns):
    assert earns_message_xp(fake_message(**kwargs)) is earns


# ---------------------------------------------------------------------- awarding


@pytest.fixture
def service(db):
    return LevelingService(db)


async def test_first_award_creates_member(service, db):
    result = await service.award_xp(1, 10, 15, XpSource.MESSAGE, name="Subash")
    assert (result.total_xp, result.old_level, result.new_level, result.leveled_up) == (15, 0, 0, False)
    async with db.session() as session:
        row = SimpleNamespace(**dict(await session.fetchrow("SELECT * FROM community.member_levels")))
    assert (row.guild_id, row.user_id, row.total_xp, row.message_xp, row.voice_xp, row.level) == (1, 10, 15, 15, 0, 0)
    assert row.last_known_name == "Subash"


async def test_awards_accumulate_by_source(service, db):
    await service.award_xp(1, 10, 10, XpSource.MESSAGE)
    await service.award_xp(1, 10, 5, XpSource.VOICE)
    await service.award_xp(1, 10, 7, XpSource.ADMIN)
    async with db.session() as session:
        row = SimpleNamespace(**dict(await session.fetchrow("SELECT * FROM community.member_levels")))
        logs = [r["source"] for r in await session.fetch("SELECT source FROM community.xp_log ORDER BY id")]
    assert (row.total_xp, row.message_xp, row.voice_xp) == (22, 10, 5)
    assert logs == ["message", "voice", "admin"]


async def test_level_up_detected_and_stored(service, db):
    assert not (await service.award_xp(1, 10, 99, XpSource.MESSAGE)).leveled_up
    result = await service.award_xp(1, 10, 1, XpSource.MESSAGE)
    assert (result.old_level, result.new_level, result.leveled_up) == (0, 1, True)
    assert not (await service.award_xp(1, 10, 1, XpSource.MESSAGE)).leveled_up
    async with db.session() as session:
        assert (await session.fetchval("SELECT level FROM community.member_levels")) == 1


async def test_multi_level_jump_reports_final_level(service):
    result = await service.award_xp(1, 10, 1_000, XpSource.ADMIN)
    assert (result.old_level, result.new_level) == (0, 3)


async def test_members_and_guilds_are_separate(service):
    await service.award_xp(1, 10, 500, XpSource.MESSAGE)
    other_user = await service.award_xp(1, 11, 5, XpSource.MESSAGE)
    other_guild = await service.award_xp(2, 10, 5, XpSource.MESSAGE)
    assert other_user.total_xp == 5 and other_guild.total_xp == 5


async def test_name_kept_when_not_given(service, db):
    await service.award_xp(1, 10, 5, XpSource.MESSAGE, name="Old name")
    await service.award_xp(1, 10, 5, XpSource.VOICE)
    await service.award_xp(1, 10, 5, XpSource.MESSAGE, name="New name")
    async with db.session() as session:
        assert (await session.fetchval("SELECT last_known_name FROM community.member_levels")) == "New name"


async def test_concurrent_awards_lose_nothing_and_level_up_once(service, db):
    # 50 awards of 10 XP at once: 500 XP total crosses levels 1 and 2 exactly once each.
    results = await asyncio.gather(*(service.award_xp(1, 10, 10, XpSource.MESSAGE) for _ in range(50)))
    assert sorted(r.total_xp for r in results) == list(range(10, 510, 10))
    level_ups = sorted(r.new_level for r in results if r.leveled_up)
    assert level_ups == [1, 2]
    async with db.session() as session:
        row = SimpleNamespace(**dict(await session.fetchrow("SELECT * FROM community.member_levels")))
    assert (row.total_xp, row.level) == (500, 2)


# ---------------------------------------------------------------------- rank


async def test_rank_positions_and_ties(service):
    assert await service.rank(1, 10) is None
    for user, xp in [(10, 300), (11, 500), (12, 300), (13, 50)]:
        await service.award_xp(1, user, xp, XpSource.MESSAGE)
    await service.award_xp(2, 99, 10_000, XpSource.MESSAGE)  # other guild doesn't count

    positions = {user: (await service.rank(1, user)).position for user in (10, 11, 12, 13)}
    assert positions == {11: 1, 10: 2, 12: 2, 13: 4}
    info = await service.rank(1, 10)
    assert (info.total_xp, info.message_xp, info.voice_xp, info.ranked_members) == (300, 300, 0, 4)


def fake_member():
    return SimpleNamespace(
        display_name="Subash",
        colour=SimpleNamespace(value=0),
        display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatar.png"),
    )


def test_rank_embed_with_and_without_xp():
    from features.community.services.leveling import RankInfo

    empty = rank_embed(fake_member(), None)
    assert "Level 0" in empty.description and "No XP yet" in empty.description

    embed = rank_embed(fake_member(), RankInfo(total_xp=650, message_xp=600, voice_xp=50, position=2, ranked_members=9))
    assert "Level 2" in embed.description and "Rank #2 of 9" in embed.description
    assert "250 / 500 XP to level 3" in embed.description
    assert [(f.name, f.value) for f in embed.fields] == [
        ("Total XP", "650"), ("Message XP", "600"), ("Voice XP", "50"),
    ]


async def test_award_many_is_one_batch(service, db):
    await service.award_xp(1, 10, 95, XpSource.MESSAGE)
    results = dict(await service.award_many(1, [(10, "A"), (11, "B")], 5, XpSource.VOICE))
    assert results[10].leveled_up and results[10].new_level == 1
    assert results[11].total_xp == 5 and not results[11].leveled_up
    async with db.session() as session:
        rows = {r["user_id"]: SimpleNamespace(**dict(r)) for r in await session.fetch("SELECT * FROM community.member_levels")}
    assert (rows[10].voice_xp, rows[10].message_xp, rows[10].level) == (5, 95, 1)
    assert rows[11].last_known_name == "B"
