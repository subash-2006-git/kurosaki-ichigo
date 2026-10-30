"""Level formula and XP awarding. The formula lives here and nowhere else."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import asyncpg

from features.community.db import CommunityDB as Database
from features.community.models import XpSource
from features.community.repositories import MemberLevelRepo, XpLogRepo

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------- formula


def xp_for_level(level: int) -> int:
    """Total XP needed to reach a level: 100 × level²."""
    return 100 * level * level


def level_for_xp(xp: int) -> int:
    """The level a total XP amount gives (inverse of xp_for_level, rounded down)."""
    return math.isqrt(max(0, xp) // 100)


@dataclass(frozen=True)
class Progress:
    level: int
    xp_into_level: int  # XP earned since reaching the current level
    xp_for_next: int  # XP between the current level and the next one

    @property
    def fraction(self) -> float:
        return self.xp_into_level / self.xp_for_next


def progress(total_xp: int) -> Progress:
    level = level_for_xp(total_xp)
    start = xp_for_level(level)
    return Progress(level=level, xp_into_level=total_xp - start, xp_for_next=xp_for_level(level + 1) - start)


# ---------------------------------------------------------------------- awarding


@dataclass(frozen=True)
class AwardResult:
    total_xp: int
    old_level: int
    new_level: int

    @property
    def leveled_up(self) -> bool:
        return self.new_level > self.old_level


@dataclass(frozen=True)
class RankInfo:
    total_xp: int
    message_xp: int
    voice_xp: int
    position: int  # 1 = top; members with equal XP share a position
    ranked_members: int


@dataclass(frozen=True)
class LeaderboardEntry:
    position: int
    user_id: int
    name: str | None  # last known display name, for members who have left
    level: int
    total_xp: int


class LevelingService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def award_xp(
        self, guild_id: int, user_id: int, amount: int, source: XpSource, *, name: str | None = None
    ) -> AwardResult:
        """Add XP and update the cached level, all in one transaction.

        SQLite holds the write lock from the first statement until commit, so concurrent
        awards for the same member are applied one after another and a level-up is only
        reported once.
        """
        async with self.db.session() as session:
            result = await self._award(session, guild_id, user_id, amount, source, name)
        self._log(guild_id, user_id, amount, source, result)
        return result

    async def award_many(
        self, guild_id: int, members: list[tuple[int, str | None]], amount: int, source: XpSource
    ) -> list[tuple[int, AwardResult]]:
        """Award the same XP to several (user_id, name) members in one transaction."""
        results: list[tuple[int, AwardResult]] = []
        async with self.db.session() as session:
            for user_id, name in members:
                results.append((user_id, await self._award(session, guild_id, user_id, amount, source, name)))
        for user_id, result in results:
            self._log(guild_id, user_id, amount, source, result)
        return results

    @staticmethod
    async def _award(
        session: asyncpg.Connection, guild_id: int, user_id: int, amount: int, source: XpSource, name: str | None
    ) -> AwardResult:
        members = MemberLevelRepo(session)
        total_xp, old_level = await members.add_xp(guild_id, user_id, amount, source, name=name)
        new_level = level_for_xp(total_xp)
        if new_level != old_level:
            await members.set_level(guild_id, user_id, new_level)
        await XpLogRepo(session).add(guild_id, user_id, source, amount)
        return AwardResult(total_xp=total_xp, old_level=old_level, new_level=new_level)

    @staticmethod
    def _log(guild_id: int, user_id: int, amount: int, source: XpSource, result: AwardResult) -> None:
        log.debug(
            "Awarded %d %s XP to %s in guild %s (total %d)", amount, source.value, user_id, guild_id, result.total_xp
        )
        if result.leveled_up:
            log.info("User %s reached level %d in guild %s", user_id, result.new_level, guild_id)

    # ------------------------------------------------------------------ admin changes

    async def set_xp(self, guild_id: int, user_id: int, total: int, *, name: str | None = None) -> AwardResult:
        return await self._admin_change(guild_id, user_id, set_to=total, name=name)

    async def adjust_xp(self, guild_id: int, user_id: int, delta: int, *, name: str | None = None) -> AwardResult:
        """Add (or, with a negative delta, remove) XP. Total never goes below 0."""
        return await self._admin_change(guild_id, user_id, delta=delta, name=name)

    async def _admin_change(self, guild_id: int, user_id: int, **change) -> AwardResult:
        async with self.db.session() as session:
            members = MemberLevelRepo(session)
            old_total, new_total, old_level = await members.change_total(guild_id, user_id, **change)
            new_level = level_for_xp(new_total)
            if new_level != old_level:
                await members.set_level(guild_id, user_id, new_level)
            if new_total != old_total:
                await XpLogRepo(session).add(guild_id, user_id, XpSource.ADMIN, new_total - old_total)
        log.info(
            "Admin changed XP of %s in guild %s: %d -> %d (level %d -> %d)",
            user_id, guild_id, old_total, new_total, old_level, new_level,
        )
        return AwardResult(total_xp=new_total, old_level=old_level, new_level=new_level)

    async def reset_member(self, guild_id: int, user_id: int) -> int | None:
        """Delete a member's XP. Returns the XP they had, or None if they had none."""
        async with self.db.session() as session:
            row = await MemberLevelRepo(session).delete(guild_id, user_id)
            if row is not None and row.total_xp:
                await XpLogRepo(session).add(guild_id, user_id, XpSource.ADMIN, -row.total_xp)
        if row is not None:
            log.info("Admin reset XP of %s in guild %s (had %d)", user_id, guild_id, row.total_xp)
        return row.total_xp if row else None

    async def reset_guild(self, guild_id: int) -> int:
        """Delete everyone's XP in a guild. Returns how many members had XP."""
        async with self.db.session() as session:
            count = await MemberLevelRepo(session).delete_guild(guild_id)
        log.warning("Admin reset all XP in guild %s (%d members)", guild_id, count)
        return count

    # ------------------------------------------------------------------ reading

    async def leaderboard(self, guild_id: int, page: int, per_page: int) -> tuple[list[LeaderboardEntry], int]:
        """One page (0-based) of the leaderboard, and the number of ranked members."""
        async with self.db.session() as session:
            members = MemberLevelRepo(session)
            total = await members.count(guild_id)
            rows = await members.page(guild_id, page * per_page, per_page)
        entries = [
            LeaderboardEntry(
                position=position,
                user_id=row.user_id,
                name=row.last_known_name,
                level=row.level,
                total_xp=row.total_xp,
            )
            for position, row in rows
        ]
        return entries, total

    async def rank(self, guild_id: int, user_id: int) -> RankInfo | None:
        """XP and leaderboard position, or None if the member has never earned XP."""
        async with self.db.session() as session:
            members = MemberLevelRepo(session)
            row = await members.get(guild_id, user_id)
            if row is None:
                return None
            position = await members.position(guild_id, row.total_xp)
            ranked = await members.count(guild_id)
        return RankInfo(
            total_xp=row.total_xp,
            message_xp=row.message_xp,
            voice_xp=row.voice_xp,
            position=position,
            ranked_members=ranked,
        )
