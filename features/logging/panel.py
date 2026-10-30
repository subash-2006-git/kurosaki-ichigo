"""Control panel section for server logging: see which log channels exist, create/repair them, stats counters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from features.logging import settings
from features.logging.cogs.setup import Setup

if TYPE_CHECKING:
    from core.bot import Bot

ADMIN_ONLY = "Creating log channels changes channel permissions, so it needs the **Administrator** permission."


async def status(bot: Bot, guild: discord.Guild) -> str:
    store = bot.log_channels
    category = store.log_category(guild)
    if category is None:
        logs = f"{style.OFF} Logs not set up"
    else:
        found = sum(1 for key in settings.LOG_CHANNELS if store.log_channel(guild, key) is not None)
        logs = f"{style.ON} Logs in {found}/{len(settings.LOG_CHANNELS)} channels"
    counters = len(store.counters(guild))
    stats = f"{style.ON} {counters} stats counters" if counters else f"{style.OFF} No stats counters"
    return f"{logs} · {stats}"


class LoggingEditor(SectionEditor):
    async def build(self) -> None:
        store = self.bot.log_channels
        category = store.log_category(self.guild)
        lines = []
        for key, (name, _topic) in settings.LOG_CHANNELS.items():
            channel = store.log_channel(self.guild, key)
            emoji = name.split("・")[0]
            lines.append(f"{emoji} {channel.mention if channel else '*missing*'}")
        counters = store.counters(self.guild)
        items: list[discord.ui.Item] = [
            self.text("## 📜 Server Logs\n"
                      "Logs every message edit and delete, join and leave, role, channel, voice and server change, "
                      "with who did it."),
            discord.ui.Separator(),
        ]
        if category is None:
            items.append(self.text(f"{style.OFF} **Not set up yet.** Press **Create log channels** to make a private "
                                   "📂 LOGGING category with one channel per kind of event."))
        else:
            items.append(self.text(f"{style.ON} **Logging to {category.mention}**\n" + " · ".join(lines)))
        items += [
            self.row(
                self.button("Create log channels" if category is None else "Repair log channels", self.create_logs,
                            emoji="🛠️", style_=discord.ButtonStyle.success if category is None else discord.ButtonStyle.secondary),
                self.button("Refresh", self.refresh_view, emoji="🔄"),
            ),
            self.text("**Staff who can read the logs**\n-# Pick a role to give it read-only access to the log channels."),
            self.row(self.role_select("Give a staff role access…", [], self.pick_staff_role)),
            discord.ui.Separator(),
            self.text(f"**📊 Server stats** · {len(counters)}/{len(settings.STATS_COUNTERS)} counters\n"
                      "-# Voice channels at the top of the server showing members, humans, bots, online and boosts."),
            self.row(self.button("Create stats counters" if not counters else "Repair stats counters", self.create_stats,
                                 emoji="📊")),
        ]
        self.add_item(self.container(*items))

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator

    async def _run_setup(self, interaction: discord.Interaction, command, *args) -> None:
        if not self._is_admin(interaction):
            await interaction.response.send_message(embed=style.fail(ADMIN_ONLY), ephemeral=True)
            return
        cog = self.bot.get_cog("Setup")
        await command.callback(cog, interaction, *args)  # the same code as /setup, replying privately
        await self.bot.panel.refresh(self.guild)

    async def create_logs(self, interaction: discord.Interaction) -> None:
        await self._run_setup(interaction, Setup.logs, None)

    async def create_stats(self, interaction: discord.Interaction) -> None:
        await self._run_setup(interaction, Setup.stats)

    async def pick_staff_role(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        role = self.guild.get_role(int(values[0])) if values else None
        if role is None:
            await interaction.response.defer()
            return
        await self._run_setup(interaction, Setup.logs, role)

    async def refresh_view(self, interaction: discord.Interaction) -> None:
        await self.rerender(interaction, changed=False)


SECTION = PanelSection(key="logging", label="Server Logs", emoji="📜", order=40, status=status, open=LoggingEditor.open)
