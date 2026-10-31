"""!disconnect all, !server-mute, !server-unmute, !server-deafen and !server-undeafen.

Each acts on everyone in a voice channel whose top role is below the bot's: the voice channel whose
chat the command is typed in, or else the one the person running it is in. It skips the person
running it, the server owner, and the roles and members picked in the control panel.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from core.ui import style
from features.community.utils.checks import is_at_or_above
from features.moderation.utils.modlog import send_log

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

PARALLEL = 5  # Discord calls at once; discord.py waits out rate limits on its own


@dataclass(frozen=True)
class VoiceAction:
    permission: str                                        # needed by the person and the bot, in that channel
    emoji: str
    done: str                                              # "Disconnected" …
    already: str                                           # why a member needed nothing: "already muted" …
    needs: Callable[[discord.Member], bool]                # whether this member still needs it
    apply: Callable[[discord.Member, str], Awaitable[object]]


ACTIONS: dict[str, VoiceAction] = {
    "disconnect": VoiceAction("move_members", "🔌", "Disconnected", "", lambda m: True,
                              lambda m, reason: m.move_to(None, reason=reason)),
    "server-mute": VoiceAction("mute_members", "🔇", "Server-muted", "already muted", lambda m: not m.voice.mute,
                               lambda m, reason: m.edit(mute=True, reason=reason)),
    "server-unmute": VoiceAction("mute_members", "🔊", "Unmuted", "not muted", lambda m: m.voice.mute,
                                 lambda m, reason: m.edit(mute=False, reason=reason)),
    "server-deafen": VoiceAction("deafen_members", "🔕", "Server-deafened", "already deafened", lambda m: not m.voice.deaf,
                                 lambda m, reason: m.edit(deafen=True, reason=reason)),
    "server-undeafen": VoiceAction("deafen_members", "🔔", "Undeafened", "not deafened", lambda m: m.voice.deaf,
                                   lambda m, reason: m.edit(deafen=False, reason=reason)),
}


# ---- who it applies to (pure, tested) ----------------------------------------------------


def plan(members, *, me, invoker_id: int, owner_id: int, settings, action: VoiceAction):
    """Split the channel's members into (targets, skipped counts)."""
    targets = []
    skipped = {"you": 0, "exempt": 0, "above": 0, "already": 0}
    exempt_roles = set(settings.exempt_role_ids)
    for member in members:
        if member.id == me.id:
            continue
        if member.id == invoker_id:
            skipped["you"] += 1
        elif member.id in settings.exempt_user_ids or any(role.id in exempt_roles for role in member.roles):
            skipped["exempt"] += 1
        elif member.id == owner_id or is_at_or_above(member.top_role, me.top_role):
            skipped["above"] += 1
        elif not action.needs(member):
            skipped["already"] += 1
        else:
            targets.append(member)
    return targets, skipped


def skipped_text(skipped: dict[str, int], action: VoiceAction) -> str:
    parts = []
    if skipped["you"]:
        parts.append("you")
    if skipped["exempt"]:
        parts.append(f"{skipped['exempt']} skipped in the panel")
    if skipped["above"]:
        parts.append(f"{skipped['above']} at or above my role")
    if skipped["already"]:
        parts.append(f"{skipped['already']} {action.already}")
    return ", ".join(parts)


def voice_channel_for(ctx: commands.Context) -> discord.VoiceChannel | discord.StageChannel | None:
    """The voice channel whose chat this was typed in, or else the one the person is in."""
    if isinstance(ctx.channel, (discord.VoiceChannel, discord.StageChannel)):
        return ctx.channel
    voice = getattr(ctx.author, "voice", None)
    return voice.channel if voice and voice.channel else None


# ---- the commands ------------------------------------------------------------------------


class VoiceControl(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    @commands.command(name="disconnect", help="Disconnect everyone below my role from this voice channel")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def disconnect(self, ctx: commands.Context, scope: str | None = None) -> None:
        await self.run(ctx, "disconnect", scope)

    @commands.command(name="server-mute", help="Server-mute everyone below my role in this voice channel")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def server_mute(self, ctx: commands.Context, scope: str | None = None) -> None:
        await self.run(ctx, "server-mute", scope)

    @commands.command(name="server-unmute", help="Undo !server-mute")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def server_unmute(self, ctx: commands.Context, scope: str | None = None) -> None:
        await self.run(ctx, "server-unmute", scope)

    @commands.command(name="server-deafen", help="Server-deafen everyone below my role in this voice channel")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def server_deafen(self, ctx: commands.Context, scope: str | None = None) -> None:
        await self.run(ctx, "server-deafen", scope)

    @commands.command(name="server-undeafen", help="Undo !server-deafen")
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def server_undeafen(self, ctx: commands.Context, scope: str | None = None) -> None:
        await self.run(ctx, "server-undeafen", scope)

    async def run(self, ctx: commands.Context, key: str, scope: str | None) -> None:
        action = ACTIONS[key]
        command = f"{ctx.prefix}{key}"
        if scope is not None and scope.lower() != "all":
            raise commands.BadArgument(f"Use `{command} all` (or just `{command}`).")
        channel = voice_channel_for(ctx)
        if channel is None:
            raise commands.BadArgument("Type this in a voice channel's chat, or join the voice channel first.")
        mine, theirs = channel.permissions_for(ctx.guild.me), channel.permissions_for(ctx.author)
        if not getattr(theirs, action.permission):
            raise commands.MissingPermissions([action.permission])
        if not getattr(mine, action.permission):
            raise commands.BotMissingPermissions([action.permission])

        settings = await self.bot.voicecontrol_db.settings(ctx.guild.id)
        targets, skipped = plan(channel.members, me=ctx.guild.me, invoker_id=ctx.author.id, owner_id=ctx.guild.owner_id,
                                settings=settings, action=action)
        reason = f"{command} in #{channel.name} by {ctx.author} ({ctx.author.id})"
        failed = await self._apply(action, targets, reason)

        done = len(targets) - failed
        text = f"{action.done} **{done}** member(s) in {channel.mention}."
        if extra := skipped_text(skipped, action):
            text += f"\n-# Skipped: {extra}."
        if failed:
            text += f"\n-# {failed} couldn't be changed (they may have just left)."
        await ctx.reply(embed=style.ok(text) if done else style.warn(text), mention_author=False)
        if done:
            await send_log(self.bot, ctx.guild, style.info(f"{action.emoji} {command}",
                                                           f"{action.done} {done} member(s) in {channel.mention}\n"
                                                           f"By {ctx.author.mention}"))

    @staticmethod
    async def _apply(action: VoiceAction, targets: list[discord.Member], reason: str) -> int:
        """Apply to every target, a few at a time. Returns how many failed."""
        gate = asyncio.Semaphore(PARALLEL)

        async def one(member: discord.Member) -> bool:
            async with gate:
                try:
                    await action.apply(member, reason)
                    return True
                except discord.HTTPException as exc:
                    log.info("Voice action on %s failed: %s", member.id, exc)
                    return False

        results = await asyncio.gather(*(one(m) for m in targets))
        return results.count(False)
