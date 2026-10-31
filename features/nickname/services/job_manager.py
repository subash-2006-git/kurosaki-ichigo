"""Background bulk rename/reset jobs, one at a time per server."""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import discord

from core import config
from core.ui.theme import brand
from features.nickname.services.nickname_planner import PLANNERS, Decision

if TYPE_CHECKING:
    from core.bot import Bot as NicknameBot

log = logging.getLogger(__name__)

SKIP_LEFT = "left the server"
PROGRESS_SAVE_EVERY = 25


@dataclass
class Job:
    id: int
    guild_id: int
    kind: str  # "apply" or "reset"
    started_by: int
    total: int
    started_at: float
    channel_id: int | None = None
    member_ids: list[int] = field(default_factory=list)
    status: str = "running"
    processed: int = 0
    changed: int = 0
    skipped: Counter = field(default_factory=Counter)
    failed: Counter = field(default_factory=Counter)
    finished_at: float | None = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    @classmethod
    def from_row(cls, row) -> "Job":
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            kind=row["kind"],
            started_by=row["started_by"],
            total=row["total"],
            started_at=row["started_at"],
            status=row["status"],
            processed=row["processed"],
            changed=row["changed"],
            skipped=Counter(row["skipped"]),
            failed=Counter(row["failed"]),
            finished_at=row["finished_at"],
        )


async def execute_change(
    bot: "NicknameBot",
    member: discord.Member,
    decision: Decision,
    source: str,
    job_id: int | None = None,
) -> str | None:
    """Apply a planned nickname change. Returns a failure reason, or None on success."""
    db = bot.nick_db
    old_nick = member.nick
    try:
        await member.edit(nick=decision.new_nick, reason=f"Nickname Manager ({source})")
    except discord.Forbidden:
        reason = "missing permission"
    except discord.NotFound:
        reason = SKIP_LEFT
    except discord.HTTPException as exc:
        reason = f"Discord error {exc.status}"
    else:
        if source == "reset":
            await db.delete_record(member.guild.id, member.id)
        else:
            await db.save_record(member.guild.id, member.id, decision.original_nick, decision.new_nick)
        await db.log_change(member.guild.id, member.id, source, old_nick, decision.new_nick, "changed", job_id=job_id)
        return None

    await db.log_change(member.guild.id, member.id, source, old_nick, decision.new_nick, "failed", reason, job_id)
    log.warning("Could not rename %s in guild %s: %s", member.id, member.guild.id, reason)
    return reason


class JobManager:
    def __init__(self, bot: "NicknameBot"):
        self.bot = bot
        self._jobs: dict[int, Job] = {}

    def current(self, guild_id: int) -> Job | None:
        job = self._jobs.get(guild_id)
        return job if job and job.running else None

    async def latest(self, guild_id: int) -> Job | None:
        """The running job, or else the most recent finished one."""
        if job := self._jobs.get(guild_id):
            return job
        row = await self.bot.nick_db.last_job(guild_id)
        return Job.from_row(row) if row else None

    async def start(self, guild: discord.Guild, kind: str, member_ids: list[int], started_by: int, channel_id: int | None) -> Job:
        if self.current(guild.id):
            raise RuntimeError("A job is already running in this server.")
        started_at = time.time()
        job_id = await self.bot.nick_db.create_job(guild.id, kind, started_by, len(member_ids), started_at)
        job = Job(
            id=job_id,
            guild_id=guild.id,
            kind=kind,
            started_by=started_by,
            total=len(member_ids),
            started_at=started_at,
            channel_id=channel_id,
            member_ids=member_ids,
        )
        job.task = asyncio.create_task(self._run(job), name=f"nick-job-{job_id}")
        self._jobs[guild.id] = job
        return job

    def stop(self, guild_id: int) -> Job | None:
        job = self.current(guild_id)
        if job:
            job.cancel.set()
        return job

    async def shutdown(self) -> None:
        tasks = [job.task for job in self._jobs.values() if job.running and job.task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job: Job) -> None:
        log.info("Job #%s (%s) started in guild %s for %d members", job.id, job.kind, job.guild_id, job.total)
        shutting_down = False
        try:
            for user_id in job.member_ids:
                if job.cancel.is_set():
                    job.status = "stopped"
                    break
                await self._process(job, user_id)
                job.processed += 1
                if job.processed % PROGRESS_SAVE_EVERY == 0:
                    await self._save(job)
            else:
                job.status = "completed"
        except asyncio.CancelledError:
            job.status = "interrupted"
            shutting_down = True
            raise
        except Exception:
            log.exception("Job #%s crashed", job.id)
            job.status = "crashed"
        finally:
            job.finished_at = time.time()
            try:
                await self._save(job)
            except Exception:
                # The row stays "running" with an old heartbeat; the next startup sweep marks it interrupted.
                log.exception("Couldn't save the final state of job #%s", job.id)
            log.info("Job #%s finished: %s", job.id, job.status)
            if not shutting_down:
                await self._report(job)

    async def _process(self, job: Job, user_id: int) -> None:
        guild = self.bot.get_guild(job.guild_id)
        member = guild.get_member(user_id) if guild else None
        if member is None:
            job.skipped[SKIP_LEFT] += 1
            return

        # Re-plan at processing time: settings, roles, or nicknames may have
        # changed since the preview.
        db = self.bot.nick_db
        planner = PLANNERS[job.kind]
        decision = planner(member, await db.get_settings(guild.id), await db.get_record(guild.id, member.id))
        if decision.skipped:
            job.skipped[decision.skip_reason] += 1
            return

        failure = await execute_change(self.bot, member, decision, job.kind, job.id)
        if failure == SKIP_LEFT:
            job.skipped[SKIP_LEFT] += 1
        elif failure:
            job.failed[failure] += 1
        else:
            job.changed += 1
            # Wait between edits, but wake up immediately if stopped.
            try:
                await asyncio.wait_for(job.cancel.wait(), timeout=config.RENAME_DELAY_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _save(self, job: Job) -> None:
        await self.bot.nick_db.update_job(
            job.id,
            status=job.status,
            processed=job.processed,
            changed=job.changed,
            skipped=job.skipped,
            failed=job.failed,
            finished_at=job.finished_at,
        )

    async def _report(self, job: Job) -> None:
        channel = self.bot.get_channel(job.channel_id) if job.channel_id else None
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(embed=brand(job_embed(job), job.guild_id))
        except discord.HTTPException:
            log.warning("Could not post the report for job #%s in channel %s", job.id, job.channel_id)


def format_counter(counter: Counter, limit: int = 1000) -> str:
    if not counter:
        return "None"
    lines = [f"{count} × {reason}" for reason, count in counter.most_common()]
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 1] + "…"


STATUS_COLOURS = {
    "running": discord.Colour.blurple(),
    "completed": discord.Colour.green(),
    "stopped": discord.Colour.orange(),
    "interrupted": discord.Colour.orange(),
    "crashed": discord.Colour.red(),
}


def job_embed(job: Job) -> discord.Embed:
    title = "Rename" if job.kind == "apply" else "Reset"
    embed = discord.Embed(
        title=f"{title} job #{job.id}: {job.status}",
        colour=STATUS_COLOURS.get(job.status, discord.Colour.greyple()),
    )
    embed.add_field(name="Progress", value=f"{job.processed} / {job.total} members")
    embed.add_field(name="Changed", value=str(job.changed))
    embed.add_field(name="Started by", value=f"<@{job.started_by}>")
    embed.add_field(name=f"Skipped ({sum(job.skipped.values())})", value=format_counter(job.skipped), inline=False)
    embed.add_field(name=f"Failed ({sum(job.failed.values())})", value=format_counter(job.failed), inline=False)
    started = f"Started <t:{int(job.started_at)}:R>"
    if job.finished_at:
        started += f" · finished <t:{int(job.finished_at)}:R>"
    embed.description = started
    return embed
