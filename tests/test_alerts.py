"""Error alerts: the throttle, what's queued from log records, and where alerts are sent."""

import asyncio
import logging
from types import SimpleNamespace

import discord
import pytest

import core.config as config
from core.alerts import Alert, AlertHandler, ErrorAlerts, Throttle, alert_embed


def test_throttle_same_error_once_per_window_with_repeat_count():
    t = Throttle(window=3600, max_per_window=10)
    a, b = ("x", "boom", "ValueError"), ("y", "other", "")
    assert t.check(a, 0) == 0
    assert t.check(a, 10) is None and t.check(a, 20) is None  # held back
    assert t.check(b, 30) == 0                                 # a different error still goes
    assert t.check(a, 3600) == 2                               # next window: says 2 were held back


def test_throttle_caps_alerts_per_window():
    t = Throttle(window=3600, max_per_window=3)
    assert [t.check(("k", str(n), ""), n) for n in range(5)] == [0, 0, 0, None, None]
    assert t.check(("k", "late", ""), 3600) == 0  # the oldest one aged out


async def test_handler_queues_error_records_only():
    queue: asyncio.Queue = asyncio.Queue()
    handler = AlertHandler(asyncio.get_running_loop(), queue, Throttle())
    logger = logging.getLogger("tests.alerts")
    logger.addHandler(handler)
    try:
        logger.warning("just a warning")
        try:
            raise ValueError("bad value")
        except ValueError:
            logger.exception("Job %s failed", 7)
        logging.getLogger("core.alerts").error("can't alert")  # never about itself
        await asyncio.sleep(0)
    finally:
        logger.removeHandler(handler)
    alert = queue.get_nowait()
    assert (alert.source, alert.message) == ("tests.alerts", "Job 7 failed")
    assert "ValueError: bad value" in alert.trace and queue.empty()


def test_embed():
    embed = alert_embed(Alert(("a", "b", ""), "features.x", "Job failed", "Traceback...\nValueError: x", 3))
    assert embed.description == "Job failed" and embed.fields[0].value == "`features.x`"
    assert embed.footer.text.startswith("Happened 3 more time(s)")


class Target:
    def __init__(self):
        self.sent = []

    async def send(self, embed=None):
        self.sent.append(embed)


async def test_sent_to_the_alert_channel_or_each_operator(monkeypatch):
    channel, dms = Target(), {1: Target(), 2: Target()}
    monkeypatch.setattr(discord.abc, "Messageable", Target)  # treat the stand-in as a channel
    bot = SimpleNamespace(get_channel=lambda i: channel if i == 50 else None, get_user=dms.get)
    alerts = ErrorAlerts(bot)

    monkeypatch.setattr(config, "ALERT_CHANNEL_ID", 50)
    await alerts._send("embed")
    assert channel.sent == ["embed"] and not dms[1].sent

    monkeypatch.setattr(config, "ALERT_CHANNEL_ID", None)
    monkeypatch.setattr(config, "OWNER_IDS", frozenset({1, 2}))
    await alerts._send("embed")
    assert dms[1].sent == ["embed"] and dms[2].sent == ["embed"]


@pytest.mark.parametrize(("team", "expected"), [(None, {9}), (SimpleNamespace(members=[SimpleNamespace(id=3)]), {3})])
async def test_operators_from_the_application(monkeypatch, team, expected):
    async def application_info():
        return SimpleNamespace(team=team, owner=SimpleNamespace(id=9))

    monkeypatch.setattr(config, "OWNER_IDS", frozenset())
    assert await ErrorAlerts(SimpleNamespace(application_info=application_info))._operators() == expected
