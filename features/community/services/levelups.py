"""What happens after XP is awarded: the level-up announcement, then the role sync."""

from __future__ import annotations

import logging

import discord

from features.community.services.guild_config import GuildConfig, GuildConfigService
from features.community.services.leveling import AwardResult
from features.community.services.roles import RoleService
from features.community.utils.checks import missing_send_permissions, resolve_channel
from features.community.utils.placeholders import render

log = logging.getLogger(__name__)

MAX_CONTENT = 2000

Messageable = discord.TextChannel | discord.Thread | discord.VoiceChannel | discord.StageChannel


def announcement_channel(
    guild: discord.Guild, config: GuildConfig, fallback: Messageable | None
) -> Messageable | None:
    """The configured level-up channel, else where the member was chatting, else nowhere."""
    if config.levelup_channel_id is not None:
        channel, problem = resolve_channel(guild, config.levelup_channel_id)
        if channel is not None and not problem:
            return channel
        log.warning("Level-up channel unusable in guild %s (%s); falling back", guild.id, problem)
    if fallback is not None and not missing_send_permissions(fallback):
        return fallback
    return None


class LevelUpService:
    def __init__(self, guild_config: GuildConfigService, roles: RoleService) -> None:
        self.guild_config = guild_config
        self.roles = roles

    async def after_award(
        self, member: discord.Member, result: AwardResult, fallback_channel: Messageable | None = None
    ) -> None:
        """Announce a level-up (if any), then make the member's level roles match. Never raises."""
        try:
            if result.leveled_up:
                await self._announce(member, result.new_level, fallback_channel)
        except Exception:
            log.exception("Level-up announcement failed for %s in guild %s", member.id, member.guild.id)
        await self.roles.sync_member(member, result.new_level)

    async def _announce(self, member: discord.Member, level: int, fallback: Messageable | None) -> None:
        config = await self.guild_config.get(member.guild.id)
        if not config.levelup_enabled:
            return
        channel = announcement_channel(member.guild, config, fallback)
        if channel is None:
            # Voice level-ups have no channel of their own; they're only announced with a level-up channel set.
            log.info("Level-up for %s in guild %s not announced: no channel", member.id, member.guild.id)
            return
        try:
            await channel.send(render(config.levelup_text, member, level=level)[:MAX_CONTENT])
        except discord.HTTPException as exc:
            log.warning("Level-up announcement failed in guild %s: %s", member.guild.id, exc)
