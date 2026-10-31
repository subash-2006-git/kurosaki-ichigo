"""Control panel section for self-roles: post the pronoun and DM-status pickers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from features.selfroles.buttons import picker_message
from features.selfroles.menus import MENUS

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    parts = []
    for menu in MENUS.values():
        posted = await bot.selfroles_db.location(guild.id, menu.key)
        parts.append(f"{style.ON if posted else style.OFF} {menu.title.split(' ', 1)[1]}")
    return " · ".join(parts)


class SelfRolesEditor(SectionEditor):
    async def build(self) -> None:
        items: list[discord.ui.Item] = [
            self.text("## 🏷️ Self roles\nPost a message where members press buttons to give themselves roles. "
                      "Roles are created the first time someone picks them, or an existing role with the same name is used."),
        ]
        if not self.guild.me.guild_permissions.manage_roles:
            items.append(self.text(f"{style.WARN} **I need the Manage Roles permission** to give out these roles."))
        for menu in MENUS.values():
            saved = await self.bot.selfroles_db.roles(self.guild.id, menu.key)
            lines = []
            for option in menu.options:
                role = self.guild.get_role(saved[option.key]) if option.key in saved else None
                if role is None:
                    role = discord.utils.find(lambda r, o=option: r.name.lower() == o.label.lower(), self.guild.roles)
                if role is None:
                    lines.append(f"{option.emoji} {option.label} · *created on first use*")
                elif role >= self.guild.me.top_role:
                    lines.append(f"{option.emoji} {role.mention} · {style.WARN} above my role")
                else:
                    lines.append(f"{option.emoji} {role.mention}")
            posted = await self.bot.selfroles_db.location(self.guild.id, menu.key)
            where = f"posted in <#{posted[0]}>" if posted else "not posted yet"
            post = discord.ui.ChannelSelect(placeholder=f"Post the {menu.title.split(' ', 1)[1].lower()} picker in…",
                                            channel_types=[discord.ChannelType.text], min_values=1, max_values=1)
            post.callback = self._post(menu.key)
            items += [
                discord.ui.Separator(),
                self.text(f"### {menu.title} · {where}\n-# {'Members pick one.' if menu.exclusive else 'Members can pick several.'}\n"
                          + "\n".join(lines)),
                self.row(post),
            ]
        self.add_item(self.container(*items))

    def _post(self, menu_key: str):
        async def callback(interaction: discord.Interaction) -> None:
            channel = self.guild.get_channel(int(interaction.data["values"][0]))
            perms = channel.permissions_for(self.guild.me) if channel else None
            if perms is None or not (perms.view_channel and perms.send_messages and perms.embed_links):
                await interaction.response.send_message(
                    embed=style.fail("I need View Channel, Send Messages and Embed Links in that channel."), ephemeral=True)
                return
            message = await channel.send(**picker_message(MENUS[menu_key], self.guild))
            previous = await self.bot.selfroles_db.location(self.guild.id, menu_key)
            if previous and previous[1] != message.id:
                old = self.guild.get_channel(previous[0])
                if isinstance(old, discord.abc.Messageable):
                    try:
                        await old.get_partial_message(previous[1]).delete()
                    except discord.HTTPException:
                        pass
            await self.bot.selfroles_db.set_location(self.guild.id, menu_key, channel.id, message.id)
            await self.rerender(interaction)
        return callback


SECTION = PanelSection(key="selfroles", label="Self roles", emoji="🏷️", order=25, status=status, open=SelfRolesEditor.open)
