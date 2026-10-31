import logging
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from features.moderation.utils import embeds
from features.moderation.utils.actions import audit_reason, kick
from features.moderation.utils.lockdown import lock_server, unlock_server
from features.moderation.utils.modlog import send_log

log = logging.getLogger(__name__)

KICK_MEMORY = 300  # seconds a kick decision is remembered for the leave event that follows


def too_new(member: discord.Member, raid: dict) -> bool:
    min_age = raid["min_account_age_days"]
    return bool(min_age) and discord.utils.utcnow() - member.created_at < timedelta(days=min_age)


class AntiRaid(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.joins: dict[int, deque[tuple[float, int]]] = defaultdict(deque)
        self.activating: set[int] = set()
        self.raid_kicks: dict[int, int] = defaultdict(int)
        # (guild_id, user_id) -> when on_member_join decided to kick them, so the leave that follows
        # can be recognised (no goodbye card for raiders) without affecting members who leave on their own.
        self._kicked: dict[tuple[int, int], float] = {}
        self.auto_end.start()

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
        await self.bot.mod_configs.update(guild.id, raidmode_active=True, raidmode_since=discord.utils.utcnow())
        self.raid_kicks[guild.id] = 0
        who = moderator or guild.me
        locked = await lock_server(self.bot, guild, audit_reason(who, f"Raid mode: {trigger}"))
        minutes = (await self.bot.mod_configs.get(guild.id)).raid["auto_end_minutes"]
        ending = (f"It turns itself off in {minutes} minute(s)." if minutes
                  else "Turn it off from the 🛡️ Moderation panel (Anti-raid) or with `/raidmode enabled:False`.")
        embed = discord.Embed(
            title="🚨 Raid mode activated",
            description=f"{trigger}\n\n🔒 Locked {locked} channel(s). New joins will be kicked.\n{ending}",
            color=discord.Color.red(),
        )
        await self._alert(guild, embed, ping=moderator is None)
        return locked

    async def deactivate(self, guild: discord.Guild, moderator: discord.abc.User, *, why: str | None = None) -> int:
        await self.bot.mod_configs.update(guild.id, raidmode_active=False, raidmode_since=None)
        unlocked = await unlock_server(self.bot, guild, audit_reason(moderator, "Raid mode ended"))
        kicked = self.raid_kicks.pop(guild.id, 0)
        self.joins.pop(guild.id, None)
        await self._alert(guild, discord.Embed(
            title="✅ Raid mode ended",
            description=f"{why or f'Ended by {moderator.mention}.'} Unlocked {unlocked} channel(s); "
                        f"kicked {kicked} member(s) who joined during raid mode.",
            color=discord.Color.green(),
        ))
        return unlocked

    async def cog_load(self) -> None:
        self.bot.join_filters.append(self.turns_away)

    async def cog_unload(self) -> None:
        self.auto_end.cancel()
        if self.turns_away in self.bot.join_filters:
            self.bot.join_filters.remove(self.turns_away)

    async def turns_away(self, member: discord.Member, *, leaving: bool = False) -> bool:
        """On join: whether on_member_join below kicks this member (raid mode, or account too new).
        On leave: whether it just did."""
        if leaving:
            kicked_at = self._kicked.pop((member.guild.id, member.id), None)
            return kicked_at is not None and time.monotonic() - kicked_at < KICK_MEMORY
        if member.bot or not member.guild.me.guild_permissions.kick_members:
            return False
        s = await self.bot.mod_configs.get(member.guild.id)
        return s.raidmode_active or (too_new(member, s.raid) and s.raid["young_account_action"] == "kick")

    def _remember_kick(self, member: discord.Member) -> None:
        now = time.monotonic()
        for key in [k for k, at in self._kicked.items() if now - at >= KICK_MEMORY]:
            del self._kicked[key]
        self._kicked[(member.guild.id, member.id)] = now

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        guild = member.guild
        s = await self.bot.mod_configs.get(guild.id)
        raid = s.raid
        can_kick = guild.me.guild_permissions.kick_members

        if s.raidmode_active and can_kick:
            self._remember_kick(member)
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
        if too_new(member, raid) and raid["young_account_action"] == "alert":
            await self._alert_young(member, min_age)
        elif too_new(member, raid) and can_kick:
            self._remember_kick(member)
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

    async def _alert_young(self, member: discord.Member, min_age: int) -> None:
        age = discord.utils.utcnow() - member.created_at
        embed = discord.Embed(
            title="🐣 New account joined",
            description=f"{member.mention} (`{member}`, {member.id}) joined with an account created "
                        f"{discord.utils.format_dt(member.created_at, 'R')}, younger than {min_age} day(s).\n"
                        "They were let in. Keep an eye on them, or kick them if they look like trouble.",
            color=discord.Color.orange(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Account age: {age.days} day(s), {age.seconds // 3600} hour(s)")
        await self._alert(member.guild, embed)

    @tasks.loop(minutes=1)
    async def auto_end(self) -> None:
        """Turn raid mode off by itself once it has been on for the server's chosen time."""
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            try:
                s = await self.bot.mod_configs.get(guild.id)
                if not s.raidmode_active:
                    continue
                if s.raidmode_since is None:  # turned on before auto-end existed: start counting now
                    await self.bot.mod_configs.update(guild.id, raidmode_since=now)
                    continue
                minutes = s.raid["auto_end_minutes"]
                if minutes and now - s.raidmode_since >= timedelta(minutes=minutes):
                    await self.deactivate(guild, guild.me, why=f"Ended automatically after {minutes} minute(s).")
                    await self.bot.panel.refresh(guild)
            except Exception:
                log.exception("Raid mode auto-end failed in %s", guild.id)

    @auto_end.before_loop
    async def before_auto_end(self) -> None:
        await self.bot.wait_until_ready()

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
