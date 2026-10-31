"""The control panel: a permanent message per server whose buttons open each feature's settings editor.

Features register a PanelSection; core never imports features. The panel's buttons are
DynamicItems (custom_id "panel:<section>"), so they keep working after restarts.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from core import config
from core.access import NOT_ALLOWED, is_allowed
from core.ui import style

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

SECTION_KEY_CHARS = "abcdefghijklmnopqrstuvwxyz_"
WHO_CAN_USE = lambda: ("Only the server owner and the members they allow (🔐 Access) can use these buttons."  # noqa: E731
                       if config.OWNER_ONLY
                       else "Only members with **Manage Server** can use these buttons.")
ROW_SIZE = 5  # buttons per row on the posted panel (Discord's maximum)

# A getting-started item: (bot, guild) -> (done, text), or None when it doesn't apply to this server.
Check = Callable[["Bot", discord.Guild], Awaitable["tuple[bool, str] | None"]]


@dataclass(frozen=True)
class PanelSection:
    key: str  # lowercase letters and _, used in the button's custom_id
    label: str
    emoji: str
    order: int  # lower comes first
    status: Callable[["Bot", discord.Guild], Awaitable[str]]  # one short line under the label
    open: Callable[[discord.Interaction], Awaitable[None]]  # send this section's private editor

    def __post_init__(self) -> None:
        if not self.key or any(c not in SECTION_KEY_CHARS for c in self.key):
            raise ValueError(f"Panel section key {self.key!r} must be lowercase letters and _")


class PanelEntry(discord.ui.DynamicItem[discord.ui.Button], template=r"panel:(?P<key>[a-z_]+)"):
    """An "Open" button on the posted panel. Restart-proof: rebuilt from its custom_id when clicked."""

    def __init__(self, key: str, *, emoji: str | None = None, label: str = "Open") -> None:
        super().__init__(
            discord.ui.Button(label=label[:80], emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"panel:{key}")
        )
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> PanelEntry:
        return cls(match["key"])

    async def callback(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if config.OWNER_ONLY:
            if not await is_allowed(interaction.client, member, interaction.guild):
                await interaction.response.send_message(embed=style.fail(NOT_ALLOWED), ephemeral=True)
                return
        elif not isinstance(member, discord.Member) or not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=style.fail("You need the **Manage Server** permission to change the bot's settings."),
                ephemeral=True,
            )
            return
        section = interaction.client.panel.section(self.key)
        if section is None:
            await interaction.response.send_message(
                embed=style.warn("That part of the panel isn't available anymore. Post a fresh panel with `/panel`."),
                ephemeral=True,
            )
            return
        await section.open(interaction)


class PanelService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._sections: dict[str, PanelSection] = {}
        self._checks: list[Check] = []

    # --- sections ---------------------------------------------------------------

    def register(self, section: PanelSection) -> None:
        self._sections[section.key] = section

    def unregister(self, key: str) -> None:
        self._sections.pop(key, None)

    def section(self, key: str) -> PanelSection | None:
        return self._sections.get(key)

    def add_check(self, check: Check) -> None:
        self._checks.append(check)

    def remove_check(self, check: Check) -> None:
        if check in self._checks:
            self._checks.remove(check)

    async def checklist(self, guild: discord.Guild) -> list[tuple[bool, str]]:
        items = []
        for check in self._checks:
            try:
                result = await check(self.bot, guild)
            except Exception:
                log.exception("Panel check %r failed in guild %s", check, guild.id)
                continue
            if result is not None:
                items.append(result)
        return items

    @property
    def sections(self) -> list[PanelSection]:
        return sorted(self._sections.values(), key=lambda s: (s.order, s.label))

    # --- the posted message -----------------------------------------------------

    async def build(self, guild: discord.Guild) -> discord.ui.LayoutView:
        theme = self.bot.theme.get(guild)
        items: list[discord.ui.Item] = [
            discord.ui.TextDisplay(
                f"# {style.GEAR} Control Panel\n"
                f"Set up **{discord.utils.escape_markdown(guild.name)}** without typing commands. "
                "Press a feature's button to change it. Changes apply instantly."
            ),
            discord.ui.Separator(),
        ]
        checklist = await self.checklist(guild)
        if checklist:
            items += [discord.ui.TextDisplay(checklist_text(checklist)), discord.ui.Separator()]
        # Statuses in blocks of up to 5, each followed by a row of its buttons. Discord allows 40
        # components per message; this costs about one per feature (a Section per feature cost three).
        sections = self.sections
        for start in range(0, len(sections), ROW_SIZE):
            block = sections[start:start + ROW_SIZE]
            lines = [f"**{section.emoji} {section.label}**\n-# {await self._status(section, guild)}" for section in block]
            if start:
                items.append(discord.ui.Separator(visible=False))
            items += [
                discord.ui.TextDisplay("\n".join(lines)),
                discord.ui.ActionRow(*(PanelEntry(s.key, emoji=s.emoji, label=s.label) for s in block)),
            ]
        items += [
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {WHO_CAN_USE()} "
                                   "Editors open privately, so only you see them."),
        ]
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(*items, accent_colour=theme.colour))
        return view

    async def _status(self, section: PanelSection, guild: discord.Guild) -> str:
        try:
            return await section.status(self.bot, guild)
        except Exception:
            log.exception("Panel status for %s failed in guild %s", section.key, guild.id)
            return "Couldn't load the status"

    async def post(self, channel: discord.TextChannel) -> discord.Message:
        """Post a fresh panel in `channel`, replacing (deleting) this server's previous one."""
        guild = channel.guild
        message = await channel.send(view=await self.build(guild))
        previous = await self._location(guild.id)
        await self.bot.db.pool.execute(
            "INSERT INTO core.panels (guild_id, channel_id, message_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id) DO UPDATE SET channel_id = EXCLUDED.channel_id, message_id = EXCLUDED.message_id, "
            "updated_at = NOW()",
            guild.id,
            channel.id,
            message.id,
        )
        if previous and previous[1] != message.id:
            old_channel = guild.get_channel(previous[0])
            if isinstance(old_channel, discord.abc.Messageable):
                try:
                    await old_channel.get_partial_message(previous[1]).delete()
                except discord.HTTPException:
                    pass
        return message

    async def refresh(self, guild: discord.Guild | None) -> None:
        """Redraw the posted panel's status lines. Never raises."""
        if guild is None:
            return
        try:
            location = await self._location(guild.id)
            if location is None:
                return
            channel = guild.get_channel(location[0])
            if not isinstance(channel, discord.abc.Messageable):
                return
            await channel.get_partial_message(location[1]).edit(view=await self.build(guild))
        except discord.NotFound:
            await self.bot.db.pool.execute("DELETE FROM core.panels WHERE guild_id = $1", guild.id)
        except Exception:
            log.warning("Couldn't refresh the panel in guild %s", guild.id, exc_info=True)

    async def _location(self, guild_id: int) -> tuple[int, int] | None:
        row = await self.bot.db.pool.fetchrow("SELECT channel_id, message_id FROM core.panels WHERE guild_id = $1", guild_id)
        return (row["channel_id"], row["message_id"]) if row else None


def checklist_text(items: list[tuple[bool, str]]) -> str:
    """The getting-started block: every item while some are left, one line once all are done."""
    if all(done for done, _ in items):
        return f"{style.OK} **Getting started: all set.**"
    left = sum(1 for done, _ in items if not done)
    lines = [f"{style.OK if done else '⬜'} {text}" for done, text in items]
    return "\n".join([f"**Getting started** · {left} left", *lines])
