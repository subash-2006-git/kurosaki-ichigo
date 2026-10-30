"""Replying to interactions whether or not they were deferred.

Discord drops an interaction that isn't answered within 3 seconds. Commands that hit the
database before replying can opt into an automatic "thinking…" defer with
`extras={"defer": True}` (on the command or any parent group), then reply through `reply()`,
which sends a follow-up once the interaction has been answered. Opt-in, not opt-out, so
commands that open modals or answer directly keep working untouched.
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

DEFER = "defer"          # True: defer before the command runs
EPHEMERAL = "ephemeral"  # False: the deferred "thinking" (and so the reply) is public


async def reply(interaction: discord.Interaction, content: str | None = None, **kwargs: Any) -> None:
    """send_message, or a follow-up if the interaction was already deferred or answered."""
    if content is not None:
        kwargs["content"] = content
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def defer(interaction: discord.Interaction, *, ephemeral: bool = False, thinking: bool = False) -> None:
    """Defer unless the interaction already has a response."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)


def _extra(command: app_commands.Command | app_commands.Group, key: str, default: Any) -> Any:
    node: app_commands.Command | app_commands.Group | None = command
    while node is not None:
        if key in node.extras:
            return node.extras[key]
        node = getattr(node, "parent", None)  # context menus have no parent
    return default


class CoreTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command = interaction.command
        if (
            interaction.type is discord.InteractionType.application_command
            and isinstance(command, app_commands.Command)
            and _extra(command, DEFER, False)
        ):
            await defer(interaction, ephemeral=_extra(command, EPHEMERAL, True), thinking=True)
        return True
