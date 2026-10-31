"""Control panel section for the voice commands: roles and members they skip."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core import config
from core.panel.editor import SectionEditor
from core.panel.service import PanelSection
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot

PERMISSIONS = {"move_members": "Move Members", "mute_members": "Mute Members", "deafen_members": "Deafen Members"}


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.voicecontrol_db.settings(guild.id)
    return f"Skips {len(s.exempt_role_ids)} role(s) and {len(s.exempt_user_ids)} member(s)"


class VoiceControlEditor(SectionEditor):
    async def build(self) -> None:
        s = await self.bot.voicecontrol_db.settings(self.guild.id)
        p = config.PREFIX
        missing = [name for perm, name in PERMISSIONS.items() if not getattr(self.guild.me.guild_permissions, perm)]
        members = discord.ui.UserSelect(
            placeholder="Members the commands skip", min_values=0, max_values=25,
            default_values=[discord.SelectDefaultValue(id=u, type=discord.SelectDefaultValueType.user)
                            for u in s.exempt_user_ids],
        )
        members.callback = self.pick_members
        intro = (f"## 🔊 Voice commands\nType these in a voice channel's chat, or anywhere while you're in the voice "
                 f"channel. They act on everyone there whose highest role is below mine.\n"
                 f"`{p}disconnect all` · `{p}server-mute` · `{p}server-unmute` · `{p}server-deafen` · `{p}server-undeafen`\n"
                 "-# Always skipped: whoever runs it and the server owner. Using them needs the matching permission "
                 "(Move, Mute or Deafen Members).")
        if missing:
            intro += f"\n{style.WARN} I'm missing {', '.join(missing)}."
        self.add_item(self.container(
            self.text(intro),
            discord.ui.Separator(),
            self.text("**Roles to skip**\n-# Members with any of these roles are never disconnected, muted or deafened."),
            self.row(self.role_select("Roles the commands skip", s.exempt_role_ids, self.pick_roles, max_values=25)),
            self.text("**Members to skip**"),
            self.row(members),
        ))

    async def pick_roles(self, interaction: discord.Interaction) -> None:
        roles = [int(v) for v in interaction.data.get("values") or []]
        await self.bot.voicecontrol_db.update(self.guild.id, exempt_role_ids=roles)
        await self.rerender(interaction)

    async def pick_members(self, interaction: discord.Interaction) -> None:
        users = [int(v) for v in interaction.data.get("values") or []]
        await self.bot.voicecontrol_db.update(self.guild.id, exempt_user_ids=users)
        await self.rerender(interaction)


SECTION = PanelSection(key="voicecontrol", label="Voice commands", emoji="🔊", order=33, status=status,
                       open=VoiceControlEditor.open)
