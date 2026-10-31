"""Which invite a member joined with: compare each invite's use count before and after the join.

One detection per join, shared by everyone who asks (the join log and the invite records), so the
server's invite list is fetched once per join. Needs Manage Server to read the invite list.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

import discord

FORGET_AFTER = 60  # seconds a join's result stays available to late askers


@dataclass(frozen=True)
class InviteResult:
    text: str                           # for the join log
    invite: discord.Invite | None       # the invite, when it could be told

    @property
    def inviter_id(self) -> int | None:
        return self.invite.inviter.id if self.invite is not None and self.invite.inviter else None

    @property
    def code(self) -> str | None:
        return self.invite.code if self.invite is not None else None


def used_invite(previous: dict[str, int] | None, invites: list[discord.Invite], vanity: str | None) -> InviteResult:
    """Decide from the use counts before (`previous`) and after (`invites`) a join."""
    if previous is None:
        return InviteResult("Unknown *(invite list was still loading)*", None)
    used = [i for i in invites if (i.uses or 0) > previous.get(i.code, 0)]
    if len(used) == 1:
        invite = used[0]
        inviter = f" by {invite.inviter.mention}" if invite.inviter else ""
        return InviteResult(f"`{invite.code}`{inviter}", invite)
    if len(used) > 1:
        return InviteResult("Ambiguous *(several invites were used at the same time)*", None)
    if vanity:
        return InviteResult(f"Unknown *(possibly the vanity URL `{vanity}`, Discovery, or a single-use invite)*", None)
    return InviteResult("Unknown *(possibly Discovery or a single-use invite)*", None)


class InviteTracker:
    def __init__(self) -> None:
        self._uses: dict[int, dict[str, int]] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._results: dict[tuple[int, int], asyncio.Task[InviteResult]] = {}

    async def load(self, guild: discord.Guild) -> None:
        if not guild.me.guild_permissions.manage_guild:
            return
        try:
            invites = await guild.invites()
        except discord.HTTPException:
            return
        self._uses[guild.id] = {i.code: i.uses or 0 for i in invites}

    def created(self, invite: discord.Invite) -> None:
        if invite.guild is not None and invite.guild.id in self._uses:
            self._uses[invite.guild.id][invite.code] = invite.uses or 0

    def deleted(self, invite: discord.Invite) -> None:
        if invite.guild is not None:
            self._uses.get(invite.guild.id, {}).pop(invite.code, None)

    def lookup(self, member: discord.Member) -> asyncio.Task[InviteResult]:
        """The invite this member just joined with. Every caller for the same join shares one detection."""
        key = (member.guild.id, member.id)
        task = self._results.get(key)
        if task is None:
            task = self._results[key] = asyncio.create_task(self._detect(member.guild))
            asyncio.get_running_loop().call_later(FORGET_AFTER, self._results.pop, key, None)
        return task

    async def _detect(self, guild: discord.Guild) -> InviteResult:
        if not guild.me.guild_permissions.manage_guild:
            return InviteResult("Unavailable *(bot lacks Manage Server)*", None)
        async with self._locks[guild.id]:
            previous = self._uses.get(guild.id)
            try:
                invites = await guild.invites()
            except discord.HTTPException:
                return InviteResult("Unavailable *(couldn't fetch invites)*", None)
            self._uses[guild.id] = {i.code: i.uses or 0 for i in invites}
        return used_invite(previous, invites, guild.vanity_url_code)
