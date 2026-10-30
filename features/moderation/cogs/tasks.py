"""Runs expired tempbans and temp roles. State lives in the database, so restarts don't lose them."""
import logging

import discord
from discord.ext import commands, tasks

from features.moderation.utils.modlog import create_case

log = logging.getLogger(__name__)


class Tasks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.run_scheduled.start()

    async def cog_unload(self) -> None:
        self.run_scheduled.cancel()

    @tasks.loop(seconds=30)
    async def run_scheduled(self):
        db = self.bot.mod_db
        if db.next_scheduled is None or db.next_scheduled > discord.utils.utcnow():
            return
        try:
            await self._run_due()
            await db.refresh_next_scheduled()
        except Exception:
            # Never let a database hiccup kill the loop; we'll retry next tick.
            log.exception("Running scheduled actions failed")

    async def _run_due(self) -> None:
        for action in await self.bot.mod_db.get_due_scheduled():
            try:
                await self._run(action)
            except discord.HTTPException as exc:
                # Leave transient failures for the next tick, but drop permanent ones.
                if isinstance(exc, (discord.Forbidden, discord.NotFound)):
                    await self.bot.mod_db.delete_scheduled(action["id"])
                log.warning("Scheduled action %s failed: %s", action["id"], exc)
            except Exception:
                log.exception("Scheduled action %s crashed", action["id"])
                await self.bot.mod_db.delete_scheduled(action["id"])

    @run_scheduled.before_loop
    async def before_run(self):
        await self.bot.wait_until_ready()

    async def _run(self, action) -> None:
        guild = self.bot.get_guild(action["guild_id"])
        if guild is None:
            # The bot was removed from that server.
            await self.bot.mod_db.delete_scheduled(action["id"])
            return

        if action["type"] == "unban":
            user = discord.Object(action["user_id"])
            try:
                await guild.unban(user, reason="Tempban expired")
            except discord.NotFound:
                pass  # Already unbanned manually.
            else:
                await create_case(self.bot, guild, user, guild.me, "unban", "Tempban expired")

        elif action["type"] == "remove_role":
            member = guild.get_member(action["user_id"])
            role = guild.get_role(action["role_id"])
            if member is not None and role is not None and role in member.roles:
                await member.remove_roles(role, reason="Temp role expired")

        await self.bot.mod_db.delete_scheduled(action["id"])


async def setup(bot):
    await bot.add_cog(Tasks(bot))
