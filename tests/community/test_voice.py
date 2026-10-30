import dataclasses
from types import SimpleNamespace

import pytest

from features.community.cogs.voice_xp import voice_member, xp_channels
from features.community.services.guild_config import GuildConfig
from features.community.services.voice import TICK_SLACK, VoiceMember, VoiceTracker, present_humans
from tests.community.test_greeting import make_config
from tests.community.test_leveling import FakeClock

G = 1  # guild id


def config(**overrides) -> GuildConfig:
    base = make_config(
        leveling_enabled=True,
        voice_xp_amount=5,
        voice_xp_interval=60,
        voice_require_others=True,
        voice_min_session=120,
        voice_ignore_muted=False,
        voice_ignore_deafened=True,
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def tracker(clock):
    return VoiceTracker(clock)


def humans(*ids, **flags):
    return [VoiceMember(user_id=i, **flags) for i in ids]


# ---------------------------------------------------------------------- eligibility


def test_bots_never_count():
    members = [VoiceMember(1), VoiceMember(2, bot=True)]
    assert [m.user_id for m in present_humans(members, config())] == [1]


def test_deafened_ignored_by_default_but_muted_counts():
    members = [VoiceMember(1, deafened=True), VoiceMember(2, muted=True), VoiceMember(3)]
    assert [m.user_id for m in present_humans(members, config())] == [2, 3]


def test_mute_and_deafen_rules_are_configurable():
    members = [VoiceMember(1, deafened=True), VoiceMember(2, muted=True), VoiceMember(3)]
    strict = config(voice_ignore_muted=True, voice_ignore_deafened=True)
    relaxed = config(voice_ignore_muted=False, voice_ignore_deafened=False)
    assert [m.user_id for m in present_humans(members, strict)] == [3]
    assert [m.user_id for m in present_humans(members, relaxed)] == [1, 2, 3]


# ---------------------------------------------------------------------- awards over time


def test_no_xp_before_min_session(tracker, clock):
    tracker.joined(G, 1)
    tracker.joined(G, 2)
    clock.now += 119
    assert tracker.collect(G, [humans(1, 2)], config()) == []
    clock.now += 1
    assert sorted(tracker.collect(G, [humans(1, 2)], config())) == [1, 2]


def test_awarded_once_per_interval(tracker, clock):
    tracker.joined(G, 1)
    tracker.joined(G, 2)
    clock.now += 120
    assert len(tracker.collect(G, [humans(1, 2)], config())) == 2
    clock.now += 30
    assert tracker.collect(G, [humans(1, 2)], config()) == []
    clock.now += 30
    assert len(tracker.collect(G, [humans(1, 2)], config())) == 2


def test_late_ticks_still_award_every_interval(tracker, clock):
    # A tick that fires a little early (59.x s after the last award) must not skip a whole minute.
    tracker.joined(G, 1)
    tracker.joined(G, 2)
    clock.now += 120
    tracker.collect(G, [humans(1, 2)], config())
    clock.now += 60 - TICK_SLACK + 0.5
    assert len(tracker.collect(G, [humans(1, 2)], config())) == 2


def test_longer_interval(tracker, clock):
    tracker.joined(G, 1)
    tracker.joined(G, 2)
    cfg = config(voice_xp_interval=300, voice_min_session=0)
    awards = 0
    for _ in range(10):  # 10 ticks, 60s apart
        awards += len(tracker.collect(G, [humans(1, 2)], cfg)) // 2
        clock.now += 60
    assert awards == 2  # at t=0 and t=300


def test_alone_earns_nothing_when_others_required(tracker, clock):
    tracker.joined(G, 1)
    clock.now += 600
    assert tracker.collect(G, [humans(1)], config()) == []
    assert tracker.collect(G, [humans(1)], config(voice_require_others=False)) == [1]


def test_bots_and_deafened_people_dont_count_as_company(tracker, clock):
    tracker.joined(G, 1)
    clock.now += 600
    channel = [VoiceMember(1), VoiceMember(2, bot=True), VoiceMember(3, deafened=True)]
    assert tracker.collect(G, [channel], config()) == []


def test_newcomer_counts_as_company_but_earns_later(tracker, clock):
    tracker.joined(G, 1)
    clock.now += 600
    tracker.joined(G, 2)
    assert tracker.collect(G, [humans(1, 2)], config()) == [1]


def test_channels_are_judged_separately(tracker, clock):
    for user in (1, 2, 3):
        tracker.joined(G, user)
    clock.now += 600
    # 1 and 2 together, 3 alone in another channel.
    assert sorted(tracker.collect(G, [humans(1, 2), humans(3)], config())) == [1, 2]


def test_leaving_resets_the_session(tracker, clock):
    tracker.joined(G, 1)
    tracker.joined(G, 2)
    clock.now += 600
    tracker.left(G, 1)
    tracker.joined(G, 1)
    assert tracker.collect(G, [humans(1, 2)], config()) == [2]


def test_moving_channels_keeps_the_session(tracker, clock):
    tracker.joined(G, 1)
    clock.now += 100
    tracker.joined(G, 1)  # a move reports the member as still connected
    clock.now += 20
    assert tracker.session_seconds(G, 1) == 120


def test_unseen_member_starts_session_at_first_tick(tracker, clock):
    tracker.joined(G, 1)
    clock.now += 600
    assert tracker.collect(G, [humans(1, 2)], config()) == [1]  # 2 was never seen joining
    clock.now += 120
    assert sorted(tracker.collect(G, [humans(1, 2)], config())) == [1, 2]


def test_guilds_are_separate(tracker, clock):
    tracker.joined(1, 10)
    tracker.joined(1, 11)
    clock.now += 600
    tracker.joined(2, 10)  # same user, different guild: fresh session
    assert sorted(tracker.collect(1, [humans(10, 11)], config())) == [10, 11]
    assert tracker.collect(2, [humans(10, 11)], config()) == []


def test_sync_guild_keeps_existing_and_drops_missing(tracker, clock):
    tracker.joined(G, 1)
    tracker.joined(G, 2)
    tracker.joined(9, 2)  # other guild untouched
    clock.now += 300
    tracker.sync_guild(G, [1, 3])
    assert tracker.session_seconds(G, 1) == 300
    assert tracker.session_seconds(G, 2) is None
    assert tracker.session_seconds(G, 3) == 0
    assert tracker.session_seconds(9, 2) == 300


# ---------------------------------------------------------------------- Discord translation


def state(**flags):
    values = dict(self_deaf=False, deaf=False, self_mute=False, mute=False, suppress=False)
    values.update(flags)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    "flags, deafened, muted",
    [
        ({}, False, False),
        ({"self_deaf": True}, True, False),
        ({"deaf": True}, True, False),
        ({"self_mute": True}, False, True),
        ({"mute": True}, False, True),
        ({"suppress": True}, False, True),  # stage audience
    ],
)
def test_voice_member_from_discord_state(flags, deafened, muted):
    member = SimpleNamespace(id=5, bot=False, voice=state(**flags))
    assert voice_member(member) == VoiceMember(5, bot=False, deafened=deafened, muted=muted)


def test_afk_channel_excluded():
    afk, general, stage = object(), object(), object()
    guild = SimpleNamespace(voice_channels=[afk, general], stage_channels=[stage], afk_channel=afk)
    assert xp_channels(guild) == [general, stage]
    guild.afk_channel = None
    assert xp_channels(guild) == [afk, general, stage]
