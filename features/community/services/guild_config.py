"""Per-guild settings with an in-memory cache, so hot paths never hit the DB just for config."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from features.community.db import CommunityDB as Database
from features.community.repositories import GuildRepo


@dataclass(frozen=True)
class GuildConfig:
    guild_id: int

    welcome_enabled: bool
    welcome_channel_id: int | None
    welcome_text: str
    welcome_gif_url: str | None
    welcome_card_enabled: bool

    goodbye_enabled: bool
    goodbye_channel_id: int | None
    goodbye_text: str
    goodbye_gif_url: str | None
    goodbye_card_enabled: bool
    goodbye_dm_enabled: bool
    goodbye_dm_text: str

    leveling_enabled: bool
    levelup_enabled: bool
    levelup_channel_id: int | None
    levelup_text: str

    message_xp_min: int
    message_xp_max: int
    message_xp_cooldown: int

    voice_xp_amount: int
    voice_xp_interval: int
    voice_require_others: bool
    voice_min_session: int
    voice_ignore_muted: bool
    voice_ignore_deafened: bool

    level_role_mode: str

    # Messages as embeds (migration 0007): stored EmbedDraft dicts (None = default), link buttons, style.
    welcome_embed: dict | None = None
    welcome_buttons: list = dataclasses.field(default_factory=list)
    welcome_use_embed: bool = True
    goodbye_embed: dict | None = None
    goodbye_buttons: list = dataclasses.field(default_factory=list)
    goodbye_use_embed: bool = True
    levelup_embed: dict | None = None
    levelup_buttons: list = dataclasses.field(default_factory=list)
    levelup_use_embed: bool = True

    @classmethod
    def from_row(cls, row: Any) -> GuildConfig:
        return cls(**{field.name: getattr(row, field.name) for field in dataclasses.fields(cls)})


class GuildConfigService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._cache: dict[int, GuildConfig] = {}

    async def get(self, guild_id: int) -> GuildConfig:
        config = self._cache.get(guild_id)
        if config is None:
            async with self.db.session() as session:
                row = await GuildRepo(session).get_or_create(guild_id)
                config = GuildConfig.from_row(row)
            self._cache[guild_id] = config
        return config

    async def update(self, guild_id: int, **fields: Any) -> GuildConfig:
        async with self.db.session() as session:
            row = await GuildRepo(session).update(guild_id, **fields)
            config = GuildConfig.from_row(row)
        # Only replace the cache after the commit succeeded.
        self._cache[guild_id] = config
        return config

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)
