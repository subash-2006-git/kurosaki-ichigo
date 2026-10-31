"""The self-role pickers: which options each has, and how a click changes a member's roles."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Option:
    key: str     # lowercase letters (or a role ID for ping roles), used in the button's custom_id
    label: str   # also the role's name
    emoji: str


@dataclass(frozen=True)
class Menu:
    key: str
    title: str
    description: str
    exclusive: bool  # True: picking one removes the others
    options: tuple[Option, ...]

    def option(self, key: str) -> Option | None:
        return next((o for o in self.options if o.key == key), None)


MENUS: dict[str, Menu] = {
    "pronouns": Menu(
        key="pronouns",
        title="🏷️ Pronouns",
        description="Pick the pronouns you go by. You can choose more than one, and press again to remove one.",
        exclusive=False,
        options=(
            Option("he", "he/him", "🔵"),
            Option("she", "she/her", "🩷"),
            Option("they", "they/them", "🟣"),
            Option("any", "any pronouns", "🌈"),
            Option("ask", "ask my pronouns", "❔"),
        ),
    ),
    "dms": Menu(
        key="dms",
        title="💬 DM status",
        description="Let people know whether they can message you. Pick one; press it again to clear it.",
        exclusive=True,
        options=(
            Option("open", "DMs open", "🟢"),
            Option("closed", "DMs closed", "🔴"),
            Option("ask", "ask to DM", "🟡"),
        ),
    ),
}


# Ping roles: the options are roles each server picks in the panel (option key = the role's ID).
PINGS = Menu(
    key="pings",
    title="🔔 Ping roles",
    description="Choose what you want to be pinged for. Press again to stop.",
    exclusive=False,
    options=(),
)
MAX_PING_ROLES = 10


def ping_menu(roles: list[tuple[int, str]]) -> Menu:
    """The ping menu for a server, from its chosen (role_id, role_name) pairs."""
    return replace(PINGS, options=tuple(Option(str(role_id), name[:80], "🔔") for role_id, name in roles))


def is_dynamic(menu_key: str) -> bool:
    return menu_key == PINGS.key


def plan_toggle(menu: Menu, current: set[str], key: str) -> tuple[list[str], list[str]]:
    """Which option keys to add and remove when a member with `current` options presses `key`."""
    if key in current:
        return [], [key]
    remove = sorted(current - {key}) if menu.exclusive else []
    return [key], remove
