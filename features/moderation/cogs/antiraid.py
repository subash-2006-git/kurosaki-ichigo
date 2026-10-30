import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from features.moderation.utils import embeds
from features.moderation.utils.actions import audit_reason, kick
from features.moderation.utils.lockdown import lock_server, unlock_server
from features.moderation.utils.modlog import send_log

log = logging.getLogger(__name__)


class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.joins: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
        self.activating: set[int] = set()
        self.raid_kicks: dict[int, int] = defaultdict(int)

    async def _alert(self, guild: discord.Guild, embed: discord.Embed, ping: bool = False) -> None:
        settings = await self.bot.mod_configs.get(guild.id)
        channel = guild.get_channel(settings.alert_channel) if settings.alert_channel else None
        if channel is None:
            return
        try:
            await channel.send("@here" if ping else None, embed=embed,
                               allowed_mentions=discord.AllowedMentions(everyone=ping))
        except discord.HTTPException as exc:
            log.warning("Raid alert failed in %s: %s", guild.id, exc)

    async def activate(self, guild: discord.Guild, trigger: str, moderator: discord.abc.User | None = None) -> int:
        """Enable raid mode: lock the server and kick new joins until it's turned off."""
        await self.bot.mod_configs.update(guild.id, raidmode_active=True)
        self.raid_kicks[guild.id] = 0
        who = moderator or guild.me
        locked = await lock_server(self.bot, guild, audit_reason(who, f"Raid mode: {trigger}"))
        embed = discord.Embed(
            title="🚨 Raid mode activated",
            description=f"{trigger}\n\n🔒 Locked {locked} channel(s). New joins will be kicked.\n"
                        "Run `/raidmode enabled:False` once the raid is over.",
            color=discord.Color.red(),
        )
        await self._alert(guild, embed, ping=moderator is None)
        return locked

    async def deactivate(self, guild: discord.Guild, moderator: discord.abc.User) -> int:
        await self.bot.mod_configs.update(guild.id, raidmode_active=False)
        unlocked = await unlock_server(self.bot, guild, audit_reason(moderator, "Raid mode ended"))
        kicked = self.raid_kicks.pop(guild.id, 0)
        self.joins.pop(guild.id, None)
        await self._alert(guild, discord.Embed(
            title="✅ Raid mode ended",
            description=f"Ended by {moderator.mention}. Unlocked {unlocked} channel(s); "
                        f"kicked {kicked} member(s) who joined during raid mode.",
            color=discord.Color.green(),
        ))
        return unlocked

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        s = await self.bot.mod_configs.get(guild.id)
        raid = s.raid
        can_kick = guild.me.guild_permissions.kick_members

        if s.raidmode_active and can_kick:
            try:
                await member.send(f"**{guild.name}** is temporarily locked down. Please try joining again later.")
            except discord.HTTPException:
                pass
            try:
                await member.kick(reason="Raid mode is active")
                self.raid_kicks[guild.id] += 1
            except discord.HTTPException:
                pass
            return

        min_age = raid["min_account_age_days"]
        if min_age and can_kick and discord.utils.utcnow() - member.created_at < timedelta(days=min_age):
            try:
                await kick(self.bot, guild, member, guild.me,
                           f"Account younger than {min_age} day(s) (anti-raid)")
            except discord.HTTPException as exc:
                log.warning("Account-age kick failed for %s: %s", member.id, exc)
            return

        if not raid["enabled"]:
            return
        now = time.monotonic()
        joins = self.joins[guild.id]
        joins.append((now, member.id))
        while joins and now - joins[0][0] > raid["seconds"]:
            joins.popleft()
        if len(joins) >= raid["joins"] and guild.id not in self.activating:
            self.activating.add(guild.id)
            try:
                recent = ", ".join(f"<@{uid}>" for _, uid in list(joins)[-20:])
                await self.activate(guild, f"Detected {len(joins)} joins in {raid['seconds']}s.\n"
                                           f"Recent joins: {recent}")
            finally:
                self.activating.discard(guild.id)

    @app_commands.command(name="raidmode", description="Turn raid mode on or off (locks the server, kicks new joins)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True, kick_members=True)
    async def raidmode(self, interaction: discord.Interaction, enabled: bool):
        s = await self.bot.mod_configs.get(interaction.guild_id)
        if enabled == s.raidmode_active:
            return await interaction.response.send_message(
                embed=embeds.error(f"Raid mode is already {'on' if enabled else 'off'}."), ephemeral=True)
        await interaction.response.defer()
        if enabled:
            locked = await self.activate(interaction.guild, f"Manually enabled by {interaction.user.mention}.",
                                         interaction.user)
            text = f"🚨 Raid mode **on**. Locked {locked} channel(s); new joins will be kicked."
        else:
            unlocked = await self.deactivate(interaction.guild, interaction.user)
            text = f"✅ Raid mode **off**. Unlocked {unlocked} channel(s)."
        await interaction.followup.send(embed=embeds.success(text))
        await send_log(self.bot, interaction.guild, embeds.info("Raid mode", f"{text}\nBy {interaction.user.mention}"))


async def setup(bot):
    await bot.add_cog(AntiRaid(bot))
