"""Hiding slash commands that the control panel replaces.

Their code stays loaded (some panel buttons reuse it); they're only taken off Discord's command list,
which keeps that list short. Set CONFIG_COMMANDS=true to keep them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord import app_commands

from core import config

if TYPE_CHECKING:
    from core.bot import Bot


def hide(bot: Bot, *paths: str) -> None:
    """Remove commands by path: "config" removes /config, "leveling role add" removes one subcommand."""
    if config.CONFIG_COMMANDS:
        return
    for path in paths:
        parts = path.split()
        if len(parts) == 1:
            bot.tree.remove_command(parts[0])
            continue
        node = bot.tree.get_command(parts[0])
        for name in parts[1:-1]:
            node = node.get_command(name) if isinstance(node, app_commands.Group) else None
        if isinstance(node, app_commands.Group):
            node.remove_command(parts[-1])
