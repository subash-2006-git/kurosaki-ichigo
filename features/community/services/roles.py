"""Level roles: which milestone roles a member should have, and making Discord match.

sync_member runs after every XP award. It's cheap when nothing needs changing (milestones are
cached and member roles come from discord.py's cache), so roles self-heal: members who were
already past a milestone when it was added, deleted roles, and role-mode changes are all fixed
the next time the member earns XP.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

import discord

from features.community.db import CommunityDB as Database
from features.community.models import RoleMode
from features.community.repositories import LevelRoleRepo, MemberLevelRepo
from features.community.services.guild_config import GuildConfigService
from features.community.utils.checks import role_problem

log = logging.getLogger(__name__)

REASON = "Level role"
PROBLEM_LOG_INTERVAL = 3600  # log the same role problem at most once an hour per guild


@dataclass(frozen=True)
class Milestone:
    level: int
    role_id: int | None  # None: create "Level N" the first time someone reaches it


def target_levels(milestones: list[Milestone], level: int, mode: str) -> list[int]:
    """The milestone levels whose roles a member at this level should have."""
    reached = sorted(m.level for m in milestones if m.level <= level)
    if mode == RoleMode.HIGHEST.value:
        return reached[-1:]
    return reached


@dataclass(frozen=True)
class SyncResult:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


class RoleService:
    def __init__(self, db: Database, guild_config: GuildConfigService) -> None:
        self.db = db
        self.guild_config = guild_config
        self._milestones: dict[int, list[Milestone]] = {}
        # Bumped on every change, so a read that started before the change can't cache the old list.
        self._versions: defaultdict[int, int] = defaultdict(int)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._problem_logged: dict[tuple[int, str], float] = {}

    # ------------------------------------------------------------------ milestones

    async def milestones(self, guild_id: int) -> list[Milestone]:
        cached = self._milestones.get(guild_id)
        if cached is None:
            version = self._versions[guild_id]
            async with self.db.session() as session:
                rows = await LevelRoleRepo(session).list(guild_id)
            cached = [Milestone(row.level, row.role_id) for row in rows]
            if self._versions[guild_id] == version:  # nothing changed while we were reading
                self._milestones[guild_id] = cached
        return cached

    def forget(self, guild_id: int) -> None:
        self._changed(guild_id)

    def _changed(self, guild_id: int) -> None:
        self._versions[guild_id] += 1
        self._milestones.pop(guild_id, None)

    async def set_milestone(self, guild_id: int, level: int, role_id: int | None) -> None:
        async with self.db.session() as session:
            await LevelRoleRepo(session).set(guild_id, level, role_id)
        self._changed(guild_id)

    async def remove_milestone(self, guild_id: int, level: int) -> bool:
        async with self.db.session() as session:
            removed = await LevelRoleRepo(session).remove(guild_id, level)
        self._changed(guild_id)
        return removed

    # ------------------------------------------------------------------ syncing

    async def sync_member(self, member: discord.Member, level: int | None = None) -> SyncResult:
        """Give/take milestone roles so the member matches their level. Never raises."""
        guild = member.guild
        try:
            milestones = await self.milestones(guild.id)
            if not milestones or member.bot:
                return SyncResult()
            if level is None:
                level = await self._stored_level(guild.id, member.id)
            config = await self.guild_config.get(guild.id)
            targets = target_levels(milestones, level, config.level_role_mode)
            # Fast path: nothing to do without an API call (the common case on every award).
            if self._already_matches(member, milestones, targets):
                return SyncResult()
            async with self._locks[guild.id]:
                return await self._sync_locked(member, milestones, targets)
        except Exception:
            log.exception("Role sync failed for %s in guild %s", member.id, guild.id)
            return SyncResult(problems=("Unexpected error; see the log",))

    async def _stored_level(self, guild_id: int, user_id: int) -> int:
        async with self.db.session() as session:
            row = await MemberLevelRepo(session).get(guild_id, user_id)
        return row.level if row else 0

    @staticmethod
    def _already_matches(member: discord.Member, milestones: list[Milestone], targets: list[int]) -> bool:
        have = {role.id for role in member.roles}
        by_level = {m.level: m.role_id for m in milestones}
        if any(by_level[level] is None or by_level[level] not in have for level in targets):
            return False
        wanted = {by_level[level] for level in targets}
        return not any(m.role_id in have and m.role_id not in wanted for m in milestones if m.role_id)

    async def _sync_locked(
        self, member: discord.Member, milestones: list[Milestone], targets: list[int]
    ) -> SyncResult:
        guild = member.guild
        # Re-read inside the lock: another sync may have just created a role.
        milestones = await self.milestones(guild.id)
        problems: list[str] = []

        wanted: list[discord.Role] = []
        for level in targets:
            role = await self._resolve_role(guild, level, milestones, problems)
            if role is not None:
                wanted.append(role)
        wanted_ids = {role.id for role in wanted}
        have_ids = {role.id for role in member.roles}

        to_add = [role for role in wanted if role.id not in have_ids]
        to_remove: list[discord.Role] = []
        # If a target role couldn't be resolved, keep the member's other level roles rather than leave them with none.
        if len(wanted) == len(targets):
            for milestone in milestones:
                role = guild.get_role(milestone.role_id) if milestone.role_id else None
                if role is not None and role.id in have_ids and role.id not in wanted_ids:
                    to_remove.append(role)

        added = await self._apply(member, to_add, "add", problems)
        removed = await self._apply(member, to_remove, "remove", problems)
        if added or removed:
            log.info(
                "Level roles for %s in guild %s: +%s -%s", member.id, guild.id, list(added) or "[]", list(removed) or "[]"
            )
        return SyncResult(added=added, removed=removed, problems=tuple(problems))

    async def _resolve_role(
        self, guild: discord.Guild, level: int, milestones: list[Milestone], problems: list[str]
    ) -> discord.Role | None:
        milestone = next(m for m in milestones if m.level == level)
        role = guild.get_role(milestone.role_id) if milestone.role_id else None
        if role is not None:
            return role

        if milestone.role_id is not None:
            log.warning("Level %d role %s was deleted in guild %s; creating a new one", level, milestone.role_id, guild.id)
        if not guild.me.guild_permissions.manage_roles:
            self._problem(guild.id, problems, f"Can't create the Level {level} role: I'm missing Manage Roles")
            return None
        try:
            role = await guild.create_role(name=f"Level {level}", reason=REASON)
        except discord.HTTPException as exc:
            self._problem(guild.id, problems, f"Can't create the Level {level} role: {exc}")
            return None
        await self.set_milestone(guild.id, level, role.id)
        log.info("Created role %s (%s) for level %d in guild %s", role.name, role.id, level, guild.id)
        return role

    async def _apply(
        self, member: discord.Member, roles: list[discord.Role], action: str, problems: list[str]
    ) -> tuple[str, ...]:
        guild = member.guild
        allowed = []
        for role in roles:
            problem = role_problem(guild, role)
            if problem:
                self._problem(guild.id, problems, f"Can't {action} {role.name}: {problem}")
            else:
                allowed.append(role)
        if not allowed:
            return ()
        try:
            if action == "add":
                await member.add_roles(*allowed, reason=REASON)
            else:
                await member.remove_roles(*allowed, reason=REASON)
        except discord.HTTPException as exc:
            self._problem(guild.id, problems, f"Can't {action} level roles: {exc}")
            return ()
        return tuple(role.name for role in allowed)

    def _problem(self, guild_id: int, problems: list[str], message: str) -> None:
        problems.append(message)
        key = (guild_id, message)
        now = time.monotonic()
        last = self._problem_logged.get(key)
        if last is None or now - last >= PROBLEM_LOG_INTERVAL:
            self._problem_logged[key] = now
            log.error("%s (guild %s)", message, guild_id)
