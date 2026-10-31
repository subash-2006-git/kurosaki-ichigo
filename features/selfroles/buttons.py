"""Self-role buttons (restart-proof DynamicItems, custom_id "srole:<menu>:<option>") and the picker messages."""

from __future__ import annotations

import logging

import discord

from core.ui import style
from core.ui.theme import brand
from features.selfroles.menus import MENUS, Menu, Option, plan_toggle

log = logging.getLogger(__name__)


async def resolve_role(bot, guild: discord.Guild, menu: Menu, option: Option) -> discord.Role | str:
    """The option's role: the saved one, else an existing role with the same name, else a new one. Or an error."""
    saved = (await bot.selfroles_db.roles(guild.id, menu.key)).get(option.key)
    role = guild.get_role(saved) if saved else None
    if role is None:
        role = discord.utils.find(lambda r: r.name.lower() == option.label.lower(), guild.roles)
        if role is None:
            if not guild.me.guild_permissions.manage_roles:
                return "I need the **Manage Roles** permission to create this role. Please tell a server admin."
            role = await guild.create_role(name=option.label, mentionable=False, reason=f"Self-role: {menu.key}")
        await bot.selfroles_db.set_role(guild.id, menu.key, option.key, role.id)
    if role >= guild.me.top_role or role.managed:
        return f"I can't give out {role.mention}: it's above my role. Please tell a server admin."
    return role


class SelfRoleButton(discord.ui.DynamicItem[discord.ui.Button], template=r"srole:(?P<menu>[a-z]+):(?P<key>[a-z]+)"):
    def __init__(self, menu: str, key: str, *, label: str | None = None, emoji: str | None = None) -> None:
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                           custom_id=f"srole:{menu}:{key}"))
        self.menu, self.key = menu, key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> SelfRoleButton:
        return cls(match["menu"], match["key"])

    async def callback(self, interaction: discord.Interaction) -> None:
        bot, guild, member = interaction.client, interaction.guild, interaction.user
        menu = MENUS.get(self.menu)
        option = menu.option(self.key) if menu else None
        if guild is None or option is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(embed=style.warn("This button is out of date."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        role = await resolve_role(bot, guild, menu, option)
        if isinstance(role, str):
            await interaction.followup.send(embed=style.fail(role), ephemeral=True)
            return

        saved = await bot.selfroles_db.roles(guild.id, menu.key)
        have = {r.id for r in member.roles}
        current = {key for key, role_id in saved.items() if role_id in have}
        add, remove = plan_toggle(menu, current, option.key)
        to_remove = [guild.get_role(saved[k]) for k in remove if k in saved]
        to_remove = [r for r in to_remove if r is not None and r < guild.me.top_role]
        try:
            if to_remove:
                await member.remove_roles(*to_remove, reason=f"Self-role: {menu.key}")
            if add:
                await member.add_roles(role, reason=f"Self-role: {menu.key}")
        except discord.HTTPException as exc:
            await interaction.followup.send(embed=style.fail(f"I couldn't change your roles: {exc.text or exc.status}."),
                                            ephemeral=True)
            return
        if add:
            text = f"Added **{option.label}**." + (f" Removed **{', '.join(r.name for r in to_remove)}**." if to_remove else "")
        else:
            text = f"Removed **{option.label}**."
        await interaction.followup.send(embed=style.ok(text), ephemeral=True)


def picker_message(menu: Menu, guild: discord.Guild) -> dict:
    embed = style.info(menu.title, menu.description)
    embed.add_field(name="Options", value="\n".join(f"{o.emoji} {o.label}" for o in menu.options))
    brand(embed, guild)
    view = discord.ui.View(timeout=None)
    for option in menu.options:
        view.add_item(SelfRoleButton(menu.key, option.key, label=option.label, emoji=option.emoji))
    return {"embed": embed, "view": view}
