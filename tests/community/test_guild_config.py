import dataclasses

import pytest

from features.community.models import DEFAULT_WELCOME_TEXT
from features.community.repositories import GUILD_SETTINGS_COLUMNS
from features.community.services.guild_config import GuildConfig, GuildConfigService


@pytest.fixture
def service(db):
    return GuildConfigService(db)


async def test_config_fields_match_table_columns(db):
    async with db.session() as session:
        rows = await session.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'community' AND table_name = 'guild_settings'"
        )
    columns = {r["column_name"] for r in rows} - {"created_at", "updated_at"}
    assert {field.name for field in dataclasses.fields(GuildConfig)} == columns
    assert GUILD_SETTINGS_COLUMNS == columns - {"guild_id"}


async def test_get_creates_defaults(service):
    config = await service.get(1)
    assert config.guild_id == 1
    assert config.welcome_enabled is False
    assert config.welcome_text == DEFAULT_WELCOME_TEXT


async def test_get_is_cached(service):
    first = await service.get(1)
    assert await service.get(1) is first


async def test_update_persists_and_refreshes_cache(service):
    await service.get(1)
    updated = await service.update(1, welcome_enabled=True, welcome_channel_id=1234)
    assert updated.welcome_enabled is True
    assert await service.get(1) is updated

    service.invalidate(1)
    reloaded = await service.get(1)
    assert reloaded.welcome_enabled is True
    assert reloaded.welcome_channel_id == 1234


async def test_update_unknown_field_leaves_cache_untouched(service):
    before = await service.get(1)
    with pytest.raises(ValueError, match="Unknown guild setting"):
        await service.update(1, not_a_setting=True)
    with pytest.raises(ValueError):
        await service.update(1, created_at=None)
    assert await service.get(1) is before
