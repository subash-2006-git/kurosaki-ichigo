from datetime import timedelta

import discord

from features.logging import settings
from features.logging.cogs.lifecycle import traceback_tail
from features.logging.services.log_router import UNAVAILABLE, add_field, format_duration, make_embed, truncate


def test_log_channel_matching_tolerates_emoji_changes():
    assert settings.log_channel_matches("💬・message-logs", "message")
    assert settings.log_channel_matches("x・message-logs", "message")
    assert not settings.log_channel_matches("message-log", "message")


def test_every_log_key_has_a_colour_and_unique_name():
    from features.logging.services.log_router import COLORS

    names = [name for name, _topic in settings.LOG_CHANNELS.values()]
    assert len(names) == len(set(names)) == 14
    assert set(settings.LOG_CHANNELS) <= set(COLORS)


def test_stats_channel_names_round_trip():
    name = settings.stats_channel_name("total", 1234)
    assert name == "👥 Total Users: 1,234"
    assert settings.stats_channel_matches(name, "total")
    assert not settings.stats_channel_matches(name, "humans")


def test_intervals_come_from_core_config():
    assert settings.STATS_UPDATE_INTERVAL == 30
    assert settings.STATS_RECONCILE_INTERVAL == 600


def test_format_duration_drops_only_leading_zero_units():
    assert format_duration(timedelta(seconds=42)) == "42s"
    assert format_duration(timedelta(minutes=5, seconds=3)) == "5m 3s"
    assert format_duration(timedelta(days=1, minutes=2)) == "1d 0h 2m 0s"
    assert format_duration(timedelta(seconds=-5)) == "0s"


def test_truncate_and_embed_limits():
    assert truncate("abc", 3) == "abc"
    assert truncate("abcd", 3) == "ab…"
    embed = make_embed("message", "t" * 300, "d" * 5000)
    assert len(embed.title) == 256 and len(embed.description) == 4096


def test_add_field_placeholder_and_cap():
    embed = discord.Embed()
    add_field(embed, "Empty", "")
    assert embed.fields[0].value == UNAVAILABLE
    for i in range(30):
        add_field(embed, str(i), "v")
    assert len(embed.fields) == 25


def test_traceback_tail_names_the_failing_function():
    def boom():
        raise ValueError("x")

    try:
        boom()
    except ValueError as exc:
        tail = traceback_tail(exc)
    assert "in boom" in tail
    assert traceback_tail(None) == "unknown"
