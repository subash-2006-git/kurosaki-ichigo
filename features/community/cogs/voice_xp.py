"""Voice XP: tracks voice sessions and awards XP every 60 seconds.

Eligibility and timing rules live in services/voice.py; this cog only translates Discord
voice states and writes the awards.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands, tasks

from core.bot import Bot as CommunityBot
from features.community.models import XpSource
from features.community.services.voice import VoiceMember, VoiceTracker
from features.community.services.xp_rules import boost_multiplier, is_no_xp

log = logging.getLogger(__name__)

TICK_SECONDS = 60


def voice_member(member: discord.Member) -> VoiceMember:
    state = member.voice
    return VoiceMember(
        user_id=member.id,
        bot=member.bot,
        deafened=bool(state and (state.self_deaf or state.deaf)),
        muted=bool(state and (state.self_mute or state.mute or state.suppress)),
    )


def xp_channels(guild: discord.Guild) -> list[discord.VoiceChannel | discord.StageChannel]:
    """Voice and stage channels that can earn XP (everything except the AFK channel)."""
    channels: list[discord.VoiceChannel | discord.StageChannel] = [*guild.voice_channels, *guild.stage_channels]
    return [channel for channel in channels if channel != guild.afk_channel]


class VoiceXpCog(commands.Cog):
    def __init__(self, bot: CommunityBot) -> None:
        self.bot = bot
        self.tracker = VoiceTracker()

    async def cog_load(self) -> None:
        self.award_loop.start()

    async def cog_unload(self) -> None:
        self.award_loop.cancel()

    # ------------------------------------------------------------------ sessions

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Also runs after reconnects: catch up on joins/leaves missed while disconnected.
        for guild in self.bot.guilds:
            present = [m.id for channel in [*guild.voice_channels, *guild.stage_channels] for m in channel.members]
            self.tracker.sync_guild(guild.id, present)
        log.info("Voice sessions synced for %d guild(s)", len(self.bot.guilds))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        if after.channel is None:
            self.tracker.left(member.guild.id, member.id)
        elif before.channel is None:
            self.tracker.joined(member.guild.id, member.id)
        # Moving between channels, muting, etc. keep the session going.

    # ------------------------------------------------------------------ awards

    @tasks.loop(seconds=TICK_SECONDS)
    async def award_loop(self) -> None:
        for guild in self.bot.guilds:
            try:
                await self._award_guild(guild)
            except Exception:
                # One broken guild must not stop voice XP everywhere else (or kill the loop).
                log.exception("Voice XP failed in guild %s", guild.id)

    @award_loop.before_loop
    async def before_award_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _award_guild(self, guild: discord.Guild) -> None:
        config = await self.bot.guild_config.get(guild.id)
        if not config.leveling_enabled:
            return

        channels = [channel for channel in xp_channels(guild) if channel.members and not is_no_xp(channel, config)]
        if not channels:
            return
        members_by_id = {m.id: m for channel in channels for m in channel.members}
        due = self.tracker.collect(
            guild.id, ([voice_member(m) for m in channel.members] for channel in channels), config
        )
        if not due:
            return

        results = await self.bot.leveling.award_many(
            guild.id,
            [(user_id, members_by_id[user_id].display_name) for user_id in due],
            config.voice_xp_amount * boost_multiplier(config),
            XpSource.VOICE,
        )
        for user_id, result in results:
            # No fallback channel: voice level-ups are announced only if a level-up channel is set.
            await self.bot.levelups.after_award(members_by_id[user_id], result)


async def setup(bot: CommunityBot) -> None:
    await bot.add_cog(VoiceXpCog(bot))
