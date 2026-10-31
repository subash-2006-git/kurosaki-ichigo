"""Control panel section: which members (besides the server owner) can use the bot here."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core import config
from core.access import MAX_MEMBERS, can_manage
from core.panel.editor import SectionEditor
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    if not config.OWNER_ONLY:
        return "Anyone with the needed permissions (OWNER_ONLY is off)"
    count = len(await bot.access.members(guild.id))
    return f"The server owner and {count} member(s)"


class AccessEditor(SectionEditor):
    async def build(self) -> None:
        members = sorted(await self.bot.access.members(self.guild.id))
        listed = " ".join(f"<@{m}>" for m in members) or "nobody yet"
        intro = (f"## 🔐 Access\nWho can use the bot's commands and this control panel here: the server owner "
                 f"(<@{self.guild.owner_id}>) and the members below.\n**Allowed:** {listed}\n"
                 "-# Commands that need a Discord permission (ban, purge…) still need it too. Buttons members use, "
                 "like opening a ticket or voting, work for everyone.")
        if not config.OWNER_ONLY:
            intro += f"\n{style.WARN} OWNER_ONLY is off on this bot, so this list isn't used right now."
        items: list[discord.ui.Item] = [self.text(intro), discord.ui.Separator()]
        if await can_manage(self.bot, discord.Object(self.author_id), self.guild):
            picker = discord.ui.UserSelect(
                placeholder="Members who can use the bot", min_values=0, max_values=MAX_MEMBERS,
                default_values=[discord.SelectDefaultValue(id=m, type=discord.SelectDefaultValueType.user)
                                for m in members],
            )
            picker.callback = self.pick
            items += [self.text(f"**Allowed members** (up to {MAX_MEMBERS})"), self.row(picker)]
        else:
            items.append(self.text("-# Only the server owner can change this list."))
        self.add_item(self.container(*items))

    async def pick(self, interaction: discord.Interaction) -> None:
        if not await can_manage(self.bot, interaction.user, self.guild):
            await interaction.response.send_message(embed=style.fail("Only the server owner can change this list."),
                                                    ephemeral=True)
            return
        user_ids = [int(v) for v in interaction.data.get("values") or []]
        await self.bot.access.set_members(self.guild.id, [u for u in user_ids if u != self.guild.owner_id])
        await self.rerender(interaction)
