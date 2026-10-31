from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from features.community.services.guild_config import GuildConfigService
from features.community.services.xp_rules import boost_multiplier, is_no_xp

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def cfg(**kw):
    base = dict(xp_boost_multiplier=1, xp_boost_until=None, no_xp_channel_ids=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_boost_only_while_running():
    assert boost_multiplier(cfg(), NOW) == 1
    assert boost_multiplier(cfg(xp_boost_multiplier=2, xp_boost_until=NOW + timedelta(hours=1)), NOW) == 2
    assert boost_multiplier(cfg(xp_boost_multiplier=3, xp_boost_until=NOW - timedelta(seconds=1)), NOW) == 1
    assert boost_multiplier(cfg(xp_boost_multiplier=1, xp_boost_until=NOW + timedelta(hours=1)), NOW) == 1


def test_no_xp_channels_categories_and_threads():
    config = cfg(no_xp_channel_ids=[10, 99])
    channel = lambda **kw: SimpleNamespace(**{"id": 1, "category_id": None, **kw})
    assert is_no_xp(channel(id=10), config)
    assert is_no_xp(channel(id=2, category_id=99), config)                          # whole category
    assert is_no_xp(SimpleNamespace(id=3, parent_id=10), config)                    # thread in a blocked channel
    assert is_no_xp(SimpleNamespace(id=4, parent_id=5, parent=SimpleNamespace(category_id=99)), config)
    assert not is_no_xp(channel(id=2, category_id=7), config)
    assert not is_no_xp(channel(id=10), cfg())


async def test_boost_and_channels_saved(db):
    service = GuildConfigService(db)
    until = NOW + timedelta(days=2)
    config = await service.update(1, xp_boost_multiplier=2, xp_boost_until=until, no_xp_channel_ids=[5, 6])
    assert (config.xp_boost_multiplier, config.xp_boost_until, config.no_xp_channel_ids) == (2, until, [5, 6])
