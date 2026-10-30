import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from features.nickname.services.job_manager import execute_change
from features.nickname.services.nickname_planner import plan_rename
from features.nickname.services.permission_checks import bot_permission_problem

if TYPE_CHECKING:
    from core.bot import Bot as NicknameBot

log = logging.getLogger(__name__)


class MemberEvents(commands.Cog):
    def __init__(self, bot: "NicknameBot"):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        settings = await self.bot.nick_db.get_settings(guild.id)
        if not settings.renames_new_members or not settings.template:
            return
        if await self.bot.member_turned_away(member):
            return  # anti-raid is kicking them
        if problem := bot_permission_problem(guild):
            log.warning("Can't rename new member in guild %s: %s", guild.id, problem)
            return

        decision = plan_rename(member, settings, await self.bot.nick_db.get_record(guild.id, member.id))
        if decision.skipped:
            log.debug("Skipped new member %s in guild %s: %s", member.id, guild.id, decision.skip_reason)
            return
        await execute_change(self.bot, member, decision, "join")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        # The privacy policy promises this: removing the bot deletes the server's data.
        if job := self.bot.nick_jobs.stop(guild.id):
            if job.task:
                await job.task
        await self.bot.nick_db.delete_guild_data(guild.id)
        log.info("Removed from guild %s; deleted its stored data", guild.id)


async def setup(bot: "NicknameBot"):
    await bot.add_cog(MemberEvents(bot))
