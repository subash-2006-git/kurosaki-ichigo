import dataclasses
from types import SimpleNamespace

import pytest

from features.community.cogs.leveling_admin import seconds, settings_embed, voice_rules_text
from features.community.models import XpSource
from features.community.services.leveling import LevelingService
from features.community.services.roles import Milestone
from features.community.utils.views import ConfirmView
from tests.community.test_greeting import make_config


@pytest.fixture
def service(db):
    return LevelingService(db)


async def logged(db) -> list[tuple[str, int]]:
    async with db.session() as session:
        return [(r["source"], r["amount"]) for r in await session.fetch("SELECT source, amount FROM community.xp_log ORDER BY id")]


# ---------------------------------------------------------------------- set / add


async def test_set_creates_member_and_computes_level(service, db):
    result = await service.set_xp(1, 10, 2_500, name="Subash")
    assert (result.total_xp, result.old_level, result.new_level) == (2_500, 0, 5)
    async with db.session() as session:
        row = SimpleNamespace(**dict(await session.fetchrow("SELECT * FROM community.member_levels")))
    assert (row.total_xp, row.level, row.last_known_name, row.message_xp) == (2_500, 5, "Subash", 0)
    assert await logged(db) == [("admin", 2_500)]


async def test_set_lower_drops_level_and_logs_the_difference(service, db):
    await service.award_xp(1, 10, 1_000, XpSource.MESSAGE)  # level 3
    result = await service.set_xp(1, 10, 150)
    assert (result.old_level, result.new_level, result.total_xp) == (3, 1, 150)
    assert (await logged(db))[-1] == ("admin", -850)


async def test_set_to_same_value_logs_nothing(service, db):
    await service.set_xp(1, 10, 500)
    await service.set_xp(1, 10, 500)
    assert await logged(db) == [("admin", 500)]


async def test_add_and_remove_never_below_zero(service, db):
    await service.award_xp(1, 10, 300, XpSource.MESSAGE)
    assert (await service.adjust_xp(1, 10, 700)).total_xp == 1_000
    result = await service.adjust_xp(1, 10, -5_000)
    assert (result.total_xp, result.new_level) == (0, 0)
    assert (await logged(db))[-2:] == [("admin", 700), ("admin", -1_000)]


async def test_admin_changes_keep_source_breakdown(service, db):
    await service.award_xp(1, 10, 40, XpSource.MESSAGE)
    await service.award_xp(1, 10, 20, XpSource.VOICE)
    await service.adjust_xp(1, 10, 100)
    info = await service.rank(1, 10)
    assert (info.total_xp, info.message_xp, info.voice_xp) == (160, 40, 20)


async def test_awards_after_admin_set_continue_from_new_total(service):
    await service.set_xp(1, 10, 395)
    result = await service.award_xp(1, 10, 10, XpSource.MESSAGE)
    assert (result.total_xp, result.old_level, result.new_level) == (405, 1, 2)


# ---------------------------------------------------------------------- reset


async def test_reset_member(service, db):
    await service.award_xp(1, 10, 900, XpSource.MESSAGE)
    assert await service.reset_member(1, 10) == 900
    assert await service.rank(1, 10) is None
    assert await service.reset_member(1, 10) is None
    assert (await logged(db))[-1] == ("admin", -900)


async def test_reset_guild_only_touches_that_guild(service):
    for user_id in (10, 11, 12):
        await service.award_xp(1, user_id, 100, XpSource.MESSAGE)
    await service.award_xp(2, 10, 100, XpSource.MESSAGE)
    assert await service.reset_guild(1) == 3
    assert (await service.leaderboard(1, 0, 10)) == ([], 0)
    assert (await service.rank(2, 10)).total_xp == 100
    assert await service.reset_guild(1) == 0


# ---------------------------------------------------------------------- text


@pytest.mark.parametrize("value, text", [(0, "0s"), (30, "30s"), (60, "1 min"), (90, "90s"), (300, "5 min")])
def test_seconds(value, text):
    assert seconds(value) == text


def config(**overrides):
    base = make_config(
        leveling_enabled=True, levelup_enabled=True, levelup_text="🎉 {user} reached **Level {level}**!",
        message_xp_min=5, message_xp_max=15, message_xp_cooldown=30,
        voice_xp_amount=5, voice_xp_interval=60, voice_require_others=True, voice_min_session=120,
        voice_ignore_muted=False, voice_ignore_deafened=True, level_role_mode="highest",
    )
    return dataclasses.replace(base, **overrides)


def test_voice_rules_text():
    assert voice_rules_text(config()).splitlines() == [
        "- Needs someone else in the channel: On",
        "- Time in voice before earning: 2 min",
        "- Muted members earn: On",
        "- Deafened members earn: Off",
        "- Bots and the AFK channel never earn",
    ]


def test_settings_embed():
    guild = SimpleNamespace(get_channel=lambda _id: None)
    embed = settings_embed(guild, config(), [Milestone(5, 1), Milestone(10, None)])
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Status"] == "✅ Enabled"
    assert fields["Message XP"] == "5–15 XP per message\nCooldown: 30s"
    assert fields["Voice XP"] == "5 XP every 1 min"
    assert fields["Level-up messages"].startswith("On · Where the member chatted")
    assert fields["Level roles"].startswith("2 milestone(s), highest only")

    deleted_channel = settings_embed(guild, config(levelup_channel_id=123, message_xp_min=10, message_xp_max=10), [])
    fields = {field.name: field.value for field in deleted_channel.fields}
    assert "⚠️ Channel was deleted" in fields["Level-up messages"]
    assert fields["Message XP"].startswith("10 XP per message")
    assert fields["Level roles"].startswith("None")


# ---------------------------------------------------------------------- confirm view


class FakeResponse:
    def __init__(self):
        self.sent, self.edited = [], []

    async def send_message(self, content, **_kwargs):
        self.sent.append(content)

    async def edit_message(self, **kwargs):
        self.edited.append(kwargs)


def fake_interaction(user_id):
    return SimpleNamespace(user=SimpleNamespace(id=user_id), response=FakeResponse())


async def test_confirm_view_only_answers_its_owner():
    view = ConfirmView(owner_id=1, confirm_label="Reset 5 members")
    assert view.confirm.label == "Reset 5 members" and view.confirmed is None
    stranger = fake_interaction(2)
    assert await view.interaction_check(stranger) is False
    assert stranger.response.sent == ["This isn't your confirmation."]
    assert await view.interaction_check(fake_interaction(1)) is True


@pytest.mark.parametrize("button, expected", [("confirm", True), ("cancel", False)])
async def test_confirm_view_records_choice_and_disables_buttons(button, expected):
    view = ConfirmView(owner_id=1)
    interaction = fake_interaction(1)
    await view._finish(interaction, expected)
    assert view.confirmed is expected
    assert all(item.disabled for item in view.children)
    assert interaction.response.edited and view.is_finished()
