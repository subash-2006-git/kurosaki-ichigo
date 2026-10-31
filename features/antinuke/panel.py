"""Control panel section for anti-nuke."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from features.antinuke.detector import ACTION_LABELS, format_limit, parse_limit

if TYPE_CHECKING:
    from core.bot import Bot

Page = Literal["general", "limits"]
PUNISHMENT_OPTIONS = [("strip_roles", "Remove all their roles"), ("kick", "Kick them"), ("ban", "Ban them")]
LIMIT_GROUPS = {
    "Channels & roles": ("channel_delete", "channel_create", "role_delete", "role_create"),
    "Members & webhooks": ("ban", "kick", "webhook_create"),
}


def hierarchy_problem(guild: discord.Guild) -> str | None:
    top = max((r for r in guild.roles if not r.is_default()), key=lambda r: r.position, default=None)
    if top is not None and guild.me.top_role != top:
        return f"My role is below {top.mention}. Anti-nuke can't stop anyone with a role above mine, so move my role to the top."
    return None


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.antinuke_db.settings(guild.id)
    if not s.enabled:
        return f"{style.OFF} Off"
    line = f"{style.ON} On · {dict(PUNISHMENT_OPTIONS)[s.punishment].lower()} · {len(s.trusted_user_ids)} trusted"
    if hierarchy_problem(guild):
        line += f" · {style.WARN} my role isn't at the top"
    return line


class AntinukeEditor(SectionEditor):
    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, page: Page = "general") -> None:
        super().__init__(bot, guild, author_id)
        self.page: Page = page

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Changing who is trusted is as sensitive as it gets: owner or Administrator only.
        if not await super().interaction_check(interaction):
            return False
        member = interaction.user
        if member.id != self.guild.owner_id and not (isinstance(member, discord.Member) and member.guild_permissions.administrator):
            await interaction.response.send_message(embed=style.fail("Only the owner or an Administrator can change anti-nuke."),
                                                    ephemeral=True)
            return False
        return True

    async def build(self) -> None:
        s = await self.bot.antinuke_db.settings(self.guild.id)
        tabs = self.row(*(
            self.button(label, self._goto(key), emoji=emoji, disabled=key == self.page,
                        style_=discord.ButtonStyle.primary if key == self.page else discord.ButtonStyle.secondary)
            for key, label, emoji in (("general", "General", "🧨"), ("limits", "Limits", "🎚️"))
        ))
        items: list[discord.ui.Item] = [
            self.text("## 🧨 Anti-nuke\nStops anyone who suddenly mass-deletes channels or roles, mass-bans, spams "
                      "webhooks, adds bots or hands out dangerous permissions, then undoes the damage."),
            tabs, discord.ui.Separator(),
        ]
        if problem := hierarchy_problem(self.guild):
            items.append(self.text(f"{style.WARN} **{problem}**"))
        items += await (self._general(s) if self.page == "general" else self._limits(s))
        self.add_item(self.container(*items))

    async def _general(self, s) -> list[discord.ui.Item]:
        trusted_users = discord.ui.UserSelect(
            placeholder="Trusted people (never stopped)", min_values=0, max_values=25,
            default_values=[discord.SelectDefaultValue(id=u, type=discord.SelectDefaultValueType.user) for u in s.trusted_user_ids[:25]],
        )
        trusted_users.callback = self.pick_users
        return [
            self.toggle("Anti-nuke", s.enabled, self.flip("enabled"), description="The server owner and I are always trusted."),
            self.text("**What happens to someone who goes over a limit**"),
            self.row(self.choice_select("Punishment", PUNISHMENT_OPTIONS, s.punishment, self.pick_punishment)),
            self.toggle("Undo the damage", s.restore, self.flip("restore"),
                        description="Recreate deleted channels and roles, delete what they created, unban who they banned."),
            self.toggle("Block untrusted bots", s.block_bot_add, self.flip("block_bot_add"),
                        description="Kick any bot added by someone who isn't trusted."),
            self.toggle("Block dangerous permissions", s.revert_dangerous_perms, self.flip("revert_dangerous_perms"),
                        description="Revert a role that's given Administrator, Ban, Manage Server and similar by someone untrusted."),
            self.text("**Trusted people and roles**\n-# Admins who set up channels and roles should be here, or they may be stopped."),
            self.row(trusted_users),
            self.row(self.role_select("Trusted roles", s.trusted_role_ids[:25], self.pick_roles, max_values=25)),
            self.text("**Alerts channel**\n-# Defaults to the moderation alerts or mod-log channel."),
            self.row(self.channel_select("Where alerts go", s.alert_channel_id, self.pick_alerts)),
        ]

    async def _limits(self, s) -> list[discord.ui.Item]:
        items: list[discord.ui.Item] = [self.text("Someone is stopped when they do **more than** this many in the time window.")]
        for group, actions in LIMIT_GROUPS.items():
            summary = " · ".join(f"{ACTION_LABELS[a]} {s.limits[a]['count']} in {s.limits[a]['seconds']}s" for a in actions)
            items.append(self.setting(group, summary, "Edit", self._edit_group(group)))
        items.append(self.row(self.button("Reset to defaults", self.reset_limits, style_=discord.ButtonStyle.danger)))
        return items

    # --- callbacks ----------------------------------------------------------------

    def _goto(self, page: Page):
        async def callback(interaction: discord.Interaction) -> None:
            self.page = page
            await self.rerender(interaction, changed=False)
        return callback

    def flip(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            s = await self.bot.antinuke_db.settings(self.guild.id)
            await self.bot.antinuke_db.update(self.guild.id, **{field: not getattr(s, field)})
            await self.rerender(interaction)
        return callback

    async def pick_punishment(self, interaction: discord.Interaction) -> None:
        await self.bot.antinuke_db.update(self.guild.id, punishment=interaction.data["values"][0])
        await self.rerender(interaction)

    async def pick_users(self, interaction: discord.Interaction) -> None:
        await self.bot.antinuke_db.update(self.guild.id, trusted_user_ids=[int(v) for v in interaction.data.get("values") or []])
        await self.rerender(interaction)

    async def pick_roles(self, interaction: discord.Interaction) -> None:
        await self.bot.antinuke_db.update(self.guild.id, trusted_role_ids=[int(v) for v in interaction.data.get("values") or []])
        await self.rerender(interaction)

    async def pick_alerts(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.antinuke_db.update(self.guild.id, alert_channel_id=int(values[0]) if values else None)
        await self.rerender(interaction)

    def _edit_group(self, group: str):
        actions = LIMIT_GROUPS[group]

        async def open_form(interaction: discord.Interaction) -> None:
            s = await self.bot.antinuke_db.settings(self.guild.id)

            async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
                limits = {k: dict(v) for k, v in s.limits.items()}
                for action, text in zip(actions, values, strict=True):
                    limits[action] = parse_limit(text, ACTION_LABELS[action])
                await self.bot.antinuke_db.update(self.guild.id, limits=limits)
                await self.rerender(modal_interaction)

            await interaction.response.send_modal(Form(group, [
                Field(ACTION_LABELS[a], format_limit(s.limits[a]), max_length=7, required=True,
                      description="count/seconds, e.g. 3/10") for a in actions
            ], save))
        return open_form

    async def reset_limits(self, interaction: discord.Interaction) -> None:
        await self.bot.antinuke_db.update(self.guild.id, limits={})
        await self.rerender(interaction)


SECTION = PanelSection(key="antinuke", label="Anti-nuke", emoji="🧨", order=32, status=status, open=AntinukeEditor.open)


async def check(bot: Bot, guild: discord.Guild) -> tuple[bool, str]:
    s = await bot.antinuke_db.settings(guild.id)
    return (s.enabled and bool(s.trusted_user_ids or s.trusted_role_ids),
            "Anti-nuke is on, with your admins added as trusted (🧨 Anti-nuke)")
