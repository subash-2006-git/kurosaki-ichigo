"""Control panel section for nicknames: mode, template, who's excluded, and preview / apply / reset."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord.utils import escape_markdown

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from features.nickname.cogs.nickname_commands import MODE_LABELS
from features.nickname.services.job_manager import job_embed
from features.nickname.services.nickname_planner import account_name
from features.nickname.services.nickname_templates import TemplateError, render, validate_template

if TYPE_CHECKING:
    from core.bot import Bot


async def status(bot: Bot, guild: discord.Guild) -> str:
    settings = await bot.nick_db.get_settings(guild.id)
    if settings.mode == "off":
        line = f"{style.OFF} Off"
    else:
        line = f"{style.ON} {MODE_LABELS[settings.mode].split(':')[0]}"
    line += f" · template `{settings.template}`" if settings.template else " · no template"
    if job := bot.nick_jobs.current(guild.id):
        line += f" · job #{job.id} running {job.processed}/{job.total}"
    return line


class NicknameEditor(SectionEditor):
    async def build(self) -> None:
        settings = await self.bot.nick_db.get_settings(self.guild.id)
        me = self.guild.get_member(self.author_id)
        example = ""
        if settings.template and me is not None:
            try:
                example = f" → **{escape_markdown(render(settings.template, account_name(me), me.name))}**"
            except TemplateError:
                pass
        members = discord.ui.UserSelect(
            placeholder="Members never renamed", min_values=0, max_values=25,
            default_values=[discord.SelectDefaultValue(id=u, type=discord.SelectDefaultValueType.user)
                            for u in sorted(settings.excluded_members)[:25]],
        )
        members.callback = self.pick_members
        job = self.bot.nick_jobs.current(self.guild.id)
        self.add_item(self.container(
            self.text("## 🏷️ Nicknames\nGive everyone a nickname from a template, like `★ {name}`."),
            discord.ui.Separator(),
            self.text("**When to rename**"),
            self.row(self.choice_select("When to rename", list(MODE_LABELS.items()), settings.mode, self.pick_mode)),
            self.setting("Template", f"`{settings.template}`{example}" if settings.template else "Not set",
                         "Edit", self.edit_template),
            self.toggle("Rename bots too", settings.include_bots, self.flip_bots),
            self.text("**Never rename**"),
            self.row(self.role_select("Roles never renamed", sorted(settings.excluded_roles)[:25], self.pick_roles,
                                      max_values=25)),
            self.row(members),
            discord.ui.Separator(),
            self.row(
                self.button("Preview", self.preview, emoji="👀"),
                self.button("Rename members", self.apply, emoji="✏️", style_=discord.ButtonStyle.primary,
                            disabled=job is not None or not settings.allows_bulk or not settings.template),
                self.button("Restore old nicknames", self.reset, emoji="↩️", disabled=job is not None),
                self.button("Stop job" if job else "Last job", self.job, emoji="⏹️" if job else "📋",
                            style_=discord.ButtonStyle.danger if job else discord.ButtonStyle.secondary),
            ),
        ))

    async def _settings(self):
        return await self.bot.nick_db.get_settings(self.guild.id)

    async def pick_mode(self, interaction: discord.Interaction) -> None:
        settings = await self._settings()
        settings.mode = interaction.data["values"][0]
        await self.bot.nick_db.save_settings(settings)
        await self.rerender(interaction)

    async def edit_template(self, interaction: discord.Interaction) -> None:
        settings = await self._settings()

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            try:
                template = validate_template(values[0])
            except TemplateError as exc:
                raise ValueError(f"That template won't work: {exc}") from None
            current = await self._settings()
            current.template = template
            await self.bot.nick_db.save_settings(current)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Nickname template", [
            Field("Template", settings.template, max_length=100, required=True, placeholder="★ {name}",
                  description="{name} = their name, {username} = their account username."),
        ], save))

    async def flip_bots(self, interaction: discord.Interaction) -> None:
        settings = await self._settings()
        settings.include_bots = not settings.include_bots
        await self.bot.nick_db.save_settings(settings)
        await self.rerender(interaction)

    async def pick_roles(self, interaction: discord.Interaction) -> None:
        settings = await self._settings()
        settings.excluded_roles = {int(v) for v in interaction.data.get("values") or []}
        await self.bot.nick_db.save_settings(settings)
        await self.rerender(interaction)

    async def pick_members(self, interaction: discord.Interaction) -> None:
        settings = await self._settings()
        settings.excluded_members = {int(v) for v in interaction.data.get("values") or []}
        await self.bot.nick_db.save_settings(settings)
        await self.rerender(interaction)

    def _cog(self):
        return self.bot.get_cog("NicknameCommands")

    async def preview(self, interaction: discord.Interaction) -> None:
        cog = self._cog()
        await cog.preview.callback(cog, interaction)

    async def apply(self, interaction: discord.Interaction) -> None:
        await self._cog()._confirm_and_start(interaction, "apply")
        await self.bot.panel.refresh(self.guild)

    async def reset(self, interaction: discord.Interaction) -> None:
        await self._cog()._confirm_and_start(interaction, "reset")
        await self.bot.panel.refresh(self.guild)

    async def job(self, interaction: discord.Interaction) -> None:
        if (running := self.bot.nick_jobs.stop(self.guild.id)) is not None:
            await interaction.response.send_message(
                embed=style.warn(f"Stopping job #{running.id} after the current member."), ephemeral=True
            )
            return
        last = await self.bot.nick_jobs.latest(self.guild.id)
        if last is None:
            await interaction.response.send_message(embed=style.info("No bulk jobs have run in this server yet."),
                                                    ephemeral=True)
            return
        await interaction.response.send_message(embed=self.bot.theme.brand(job_embed(last), self.guild), ephemeral=True)


SECTION = PanelSection(key="nicknames", label="Nicknames", emoji="🏷️", order=50, status=status, open=NicknameEditor.open)
