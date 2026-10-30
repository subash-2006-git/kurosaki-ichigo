import logging
import re
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

import discord
from discord.ext import commands, tasks

from features.moderation.utils import actions
from features.moderation.utils.checks import bot_hierarchy_error
from features.moderation.utils.guild_cache import GuildSettings

log = logging.getLogger(__name__)

INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg|discord\.me|dsc\.gg)/[\w-]+", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
# Don't stack several automatic warnings on one person during a single burst of spam.
WARN_COOLDOWN = 15


class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.message_times: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self.recent_content: dict[tuple[int, int], deque[tuple[str, float]]] = defaultdict(deque)
        self.last_warned: dict[tuple[int, int], float] = {}
        self.cleanup.start()

    async def cog_unload(self) -> None:
        self.cleanup.cancel()

    @tasks.loop(minutes=10)
    async def cleanup(self):
        """Forget members who haven't spoken recently so the trackers don't grow forever."""
        cutoff = time.monotonic() - 600
        for key in [k for k, q in self.message_times.items() if not q or q[-1] < cutoff]:
            del self.message_times[key]
        for key in [k for k, q in self.recent_content.items() if not q or q[-1][1] < cutoff]:
            del self.recent_content[key]
        for key in [k for k, t in self.last_warned.items() if t < cutoff]:
            del self.last_warned[key]

    # ---- checks -------------------------------------------------------

    @staticmethod
    def _link_allowed(url: str, whitelist: set[str]) -> bool:
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        return any(host == d or host.endswith("." + d) for d in whitelist)

    def check_content(self, message: discord.Message, s: GuildSettings) -> str | None:
        """Checks that only look at the message itself (also used for edits)."""
        am = s.automod
        content = message.content
        if am["badwords"] and s.word_pattern and s.word_pattern.search(content):
            return "Use of a banned word"
        if am["invites"] and INVITE_RE.search(content):
            return "Posting server invites"
        if am["links"]:
            for url in URL_RE.findall(content):
                if not INVITE_RE.search(url) and not self._link_allowed(url, s.whitelist):
                    return "Posting links that aren't whitelisted"
        if am["mentions"]["enabled"]:
            mentions = len(set(message.raw_mentions)) + len(set(message.raw_role_mentions))
            if mentions > am["mentions"]["max"]:
                return f"Mass mentions ({mentions})"
        return None

    def check_rate(self, message: discord.Message, s: GuildSettings) -> str | None:
        """Spam and duplicate checks, which track history per member."""
        am = s.automod
        key = (message.guild.id, message.author.id)
        now = time.monotonic()

        if am["spam"]["enabled"]:
            times = self.message_times[key]
            times.append(now)
            while times and now - times[0] > am["spam"]["seconds"]:
                times.popleft()
            if len(times) > am["spam"]["messages"]:
                return "Sending messages too fast"

        if am["duplicates"]["enabled"] and message.content:
            recent = self.recent_content[key]
            normalized = message.content.strip().lower()
            recent.append((normalized, now))
            while recent and now - recent[0][1] > am["duplicates"]["seconds"]:
                recent.popleft()
            if sum(1 for text, _ in recent if text == normalized) >= am["duplicates"]["count"]:
                return "Repeating the same message"
        return None

    def _exempt(self, message: discord.Message, s: GuildSettings) -> bool:
        author = message.author
        if not isinstance(author, discord.Member) or author.bot:
            return True
        perms = message.channel.permissions_for(author)
        if perms.administrator or perms.manage_messages:
            return True
        am = s.automod
        channel_id = getattr(message.channel, "parent_id", None) or message.channel.id
        if message.channel.id in am["exempt_channels"] or channel_id in am["exempt_channels"]:
            return True
        return any(role.id in am["exempt_roles"] for role in author.roles)

    # ---- enforcement --------------------------------------------------

    async def punish(self, message: discord.Message, reason: str) -> None:
        guild, member = message.guild, message.author
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        key = (guild.id, member.id)
        now = time.monotonic()
        if now - self.last_warned.get(key, 0) < WARN_COOLDOWN:
            return
        self.last_warned[key] = now

        try:
            await message.channel.send(f"{member.mention}, your message was removed: **{reason}**.",
                                       delete_after=8)
        except discord.HTTPException:
            pass
        if bot_hierarchy_error(guild, member):
            return
        try:
            await actions.warn(self.bot, guild, member, guild.me, f"[AutoMod] {reason}")
        except discord.HTTPException as exc:
            log.warning("AutoMod warn failed for %s in %s: %s", member.id, guild.id, exc)

    async def _handle(self, message: discord.Message, *, edited: bool = False) -> None:
        if message.guild is None or message.author.bot:
            return
        s = await self.bot.mod_configs.get(message.guild.id)
        if not s.automod["enabled"] or self._exempt(message, s):
            return
        reason = self.check_content(message, s)
        if reason is None and not edited:
            reason = self.check_rate(message, s)
        if reason:
            await self.punish(message, reason)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._handle(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.content != after.content:
            await self._handle(after, edited=True)


async def setup(bot):
    await bot.add_cog(AutoMod(bot))
