"""ChannelStore: logging channels keep working after renames and moves."""

import asyncio

import discord
import pytest

from features.logging import settings
from features.logging.services.channel_store import LOG_CATEGORY, ChannelStore, log_key, stat_key


class FakePool:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.writes: list[tuple] = []

    async def fetch(self, query):
        return self.rows

    async def executemany(self, query, args):
        self.writes.extend(args)

    async def execute(self, query, *args):
        self.writes.append(("DELETE", *args))


def make_guild(guild_id=1):
    guild = discord.Guild.__new__(discord.Guild)
    guild.id = guild_id
    guild._channels = {}
    return guild


def add(guild, cls, channel_id, name, category=None, position=0):
    channel = cls.__new__(cls)
    channel.id, channel.name, channel.guild, channel.position = channel_id, name, guild, position
    channel.category_id = category.id if category else None
    guild._channels[channel_id] = channel
    return channel


async def settle():
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.fixture
def guild():
    g = make_guild()
    category = add(g, discord.CategoryChannel, 100, settings.LOG_CATEGORY_NAME)
    add(g, discord.TextChannel, 101, settings.LOG_CHANNELS["message"][0], category)
    return g


async def test_legacy_setup_is_found_by_name_then_remembered_by_id(guild):
    pool = FakePool()
    store = ChannelStore(pool)
    channel = store.log_channel(guild, "message")
    assert channel is not None and channel.id == 101
    await settle()
    assert (1, LOG_CATEGORY, 100) in pool.writes and (1, log_key("message"), 101) in pool.writes

    # Rename both the category and the channel: still found, by ID.
    guild._channels[100].name = "my logs"
    guild._channels[101].name = "chat-history"
    assert store.log_category(guild).id == 100
    assert store.log_channel(guild, "message").id == 101


async def test_ids_survive_restart_after_rename(guild):
    guild._channels[100].name = "renamed category"
    guild._channels[101].name = "renamed"
    store = ChannelStore(FakePool([
        {"guild_id": 1, "key": LOG_CATEGORY, "channel_id": 100},
        {"guild_id": 1, "key": log_key("message"), "channel_id": 101},
    ]))
    await store.load()
    assert store.log_category(guild).id == 100
    assert store.log_channel(guild, "message").id == 101
    assert store.is_log_channel(guild._channels[101])


async def test_channel_moved_out_of_category_still_receives_logs(guild):
    store = ChannelStore(FakePool())
    await store.save(1, {LOG_CATEGORY: 100, log_key("message"): 101})
    guild._channels[101].category_id = None
    assert store.log_channel(guild, "message").id == 101
    assert store.is_log_channel(guild._channels[101])


async def test_deleted_channel_is_forgotten(guild):
    pool = FakePool()
    store = ChannelStore(pool)
    await store.save(1, {LOG_CATEGORY: 100, log_key("message"): 101})
    del guild._channels[101]
    store.forget_channel(1, 101)
    await settle()
    assert ("DELETE", 1, 101) in pool.writes
    assert store.log_channel(guild, "message") is None


async def test_no_category_means_logging_disabled():
    store = ChannelStore(FakePool())
    assert store.log_category(make_guild()) is None


async def test_counters_by_id_and_is_counter():
    g = make_guild()
    category = add(g, discord.CategoryChannel, 200, settings.STATS_CATEGORY_NAME)
    total = add(g, discord.VoiceChannel, 201, settings.stats_channel_name("total", 5), category)
    store = ChannelStore(FakePool())
    assert store.counters(g) == {"total": total}  # found by label, then remembered
    await settle()
    category.name, total.name = "stats", "members"
    assert store.counters(g) == {"total": total}
    assert store.is_counter(total)
    assert not store.is_counter(category)
    assert store._ids[1][stat_key("total")] == 201
