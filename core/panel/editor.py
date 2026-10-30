"""Building blocks for a panel section's private editor.

An editor is an ephemeral Components V2 message (LayoutView) that only the admin who opened it
can use. Subclasses implement `build()`, which (re)creates the items from current settings;
after every change, `rerender()` redraws it and refreshes the posted panel.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import discord

from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

EDITOR_TIMEOUT = 600
Callback = Callable[[discord.Interaction], Awaitable[None]]


class Field:
    """One text box in a Form."""

    def __init__(self, label: str, default: str | None = None, *, description: str | None = None,
                 placeholder: str | None = None, max_length: int = 400, paragraph: bool = False,
                 required: bool = False) -> None:
        self.label = label
        self.input = discord.ui.TextInput(
            default=default,
            placeholder=placeholder,
            max_length=max_length,
            required=required,
            style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
        )
        self.description = description

    @property
    def value(self) -> str:
        return str(self.input.value or "").strip()


class Form(discord.ui.Modal):
    """A pop-up form. `on_submit(interaction, values)` gets the stripped answers in order; raise
    ValueError with a friendly message to reject them."""

    def __init__(self, title: str, fields: Sequence[Field],
                 on_submit: Callable[[discord.Interaction, list[str]], Awaitable[None]]) -> None:
        super().__init__(title=title[:45], timeout=EDITOR_TIMEOUT)
        self.fields = list(fields)
        self._on_submit = on_submit
        for f in self.fields:
            self.add_item(discord.ui.Label(text=f.label[:45], description=f.description, component=f.input))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            await self._on_submit(interaction, [f.value for f in self.fields])
        except ValueError as exc:
            if interaction.response.is_done():
                await interaction.followup.send(embed=style.fail(str(exc)), ephemeral=True)
            else:
                await interaction.response.send_message(embed=style.fail(str(exc)), ephemeral=True)


class SectionEditor(discord.ui.LayoutView):
    """Base for a section's private editor."""

    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int) -> None:
        super().__init__(timeout=EDITOR_TIMEOUT)
        self.bot = bot
        self.guild = guild
        self.author_id = author_id
        self.last_interaction: discord.Interaction | None = None

    # --- lifecycle ----------------------------------------------------------------

    @classmethod
    async def open(cls, interaction: discord.Interaction, **kwargs: Any) -> None:
        editor = cls(interaction.client, interaction.guild, interaction.user.id, **kwargs)
        editor.last_interaction = interaction
        await editor.build()
        await interaction.response.send_message(view=editor, ephemeral=True)

    async def build(self) -> None:
        raise NotImplementedError

    async def rerender(self, interaction: discord.Interaction, *, changed: bool = True) -> None:
        """Redraw this editor from current settings; if something changed, refresh the posted panel too."""
        self.last_interaction = interaction
        self.clear_items()
        await self.build()
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)
        if changed:
            await self.bot.panel.refresh(self.guild)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=style.fail("Only the person who opened this editor can use it."), ephemeral=True
            )
            return False
        self.last_interaction = interaction
        return True

    async def on_timeout(self) -> None:
        if self.last_interaction is None:
            return
        closed = discord.ui.LayoutView()
        closed.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"{style.OFF} This editor closed after 10 minutes. Press **Open** on the panel again."),
        ))
        try:
            await self.last_interaction.edit_original_response(view=closed)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.error("Panel editor %s failed in guild %s", type(self).__name__, self.guild.id, exc_info=error)
        await self.bot.report_error(self.guild, f"Panel error in {type(self).__name__}", error)
        message = style.fail("Something went wrong while saving that. It has been logged.")
        if interaction.response.is_done():
            await interaction.followup.send(embed=message, ephemeral=True)
        else:
            await interaction.response.send_message(embed=message, ephemeral=True)

    # --- building blocks ----------------------------------------------------------

    def container(self, *items: discord.ui.Item) -> discord.ui.Container:
        return discord.ui.Container(*items, accent_colour=self.bot.theme.get(self.guild).colour)

    @staticmethod
    def text(content: str) -> discord.ui.TextDisplay:
        return discord.ui.TextDisplay(content)

    @staticmethod
    def button(label: str, callback: Callback, *, style_: discord.ButtonStyle = discord.ButtonStyle.secondary,
               emoji: str | None = None, disabled: bool = False) -> discord.ui.Button:
        item = discord.ui.Button(label=label, style=style_, emoji=emoji, disabled=disabled)
        item.callback = callback
        return item

    def toggle(self, title: str, value: bool, callback: Callback, *, description: str | None = None) -> discord.ui.Section:
        """A setting line with an On/Off button on the right."""
        line = f"**{title}**" + (f"\n-# {description}" if description else "")
        state = self.button("On" if value else "Off", callback,
                            style_=discord.ButtonStyle.success if value else discord.ButtonStyle.secondary,
                            emoji=style.ON if value else style.OFF)
        return discord.ui.Section(discord.ui.TextDisplay(line), accessory=state)

    def setting(self, title: str, value: str, button_label: str, callback: Callback) -> discord.ui.Section:
        """A setting line showing its value, with an edit button on the right."""
        return discord.ui.Section(
            discord.ui.TextDisplay(f"**{title}**\n-# {value}"),
            accessory=self.button(button_label, callback),
        )

    @staticmethod
    def row(*items: discord.ui.Item) -> discord.ui.ActionRow:
        return discord.ui.ActionRow(*items)

    def channel_select(self, placeholder: str, current_id: int | None, callback: Callback, *,
                       types: Sequence[discord.ChannelType] = (discord.ChannelType.text, discord.ChannelType.news),
                       allow_none: bool = True) -> discord.ui.ChannelSelect:
        select = discord.ui.ChannelSelect(
            placeholder=placeholder,
            channel_types=list(types),
            min_values=0 if allow_none else 1,
            max_values=1,
            default_values=[discord.SelectDefaultValue(id=current_id, type=discord.SelectDefaultValueType.channel)]
            if current_id else [],
        )
        select.callback = callback
        return select

    def role_select(self, placeholder: str, current_ids: Sequence[int], callback: Callback, *,
                    max_values: int = 1) -> discord.ui.RoleSelect:
        select = discord.ui.RoleSelect(
            placeholder=placeholder,
            min_values=0,
            max_values=max_values,
            default_values=[discord.SelectDefaultValue(id=r, type=discord.SelectDefaultValueType.role) for r in current_ids],
        )
        select.callback = callback
        return select

    @staticmethod
    def choice_select(placeholder: str, options: Sequence[tuple[str, str]], current: str | None,
                      callback: Callback, *, emojis: dict[str, str] | None = None) -> discord.ui.Select:
        """A single-choice menu of (value, label) options."""
        select = discord.ui.Select(
            placeholder=placeholder,
            options=[
                discord.SelectOption(label=label, value=value, default=value == current,
                                     emoji=(emojis or {}).get(value))
                for value, label in options
            ],
        )
        select.callback = callback
        return select
