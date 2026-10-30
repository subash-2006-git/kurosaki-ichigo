"""Reusable Discord UI components."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

OnSave = Callable[[discord.Interaction, str], Awaitable[None]]


class TemplateModal(discord.ui.Modal):
    """A multi-line text editor (slash-command options can't contain line breaks)."""

    def __init__(self, *, title: str, label: str, current: str, max_length: int, on_save: OnSave) -> None:
        super().__init__(title=title, timeout=600)
        self._on_save = on_save
        self.text = discord.ui.TextInput(
            label=label,
            style=discord.TextStyle.paragraph,
            default=current[:max_length],
            max_length=max_length,
            required=True,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Saving touches the database, so acknowledge within Discord's 3 seconds first.
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._on_save(interaction, self.text.value.strip())


class ConfirmView(discord.ui.View):
    """Confirm/Cancel buttons for one user. After wait(), confirmed is True, False, or None (timed out)."""

    def __init__(self, owner_id: int, *, confirm_label: str = "Confirm", timeout: float = 30) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.confirmed: bool | None = None
        self.confirm.label = confirm_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This isn't your confirmation.", ephemeral=True)
        return False

    async def _finish(self, interaction: discord.Interaction, confirmed: bool) -> None:
        self.confirmed = confirmed
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._finish(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._finish(interaction, False)
