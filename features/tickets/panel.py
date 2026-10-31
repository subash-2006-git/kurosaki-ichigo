"""Control panel section for tickets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from core.ui.embed_draft import EmbedDraft
from core.ui.embed_editor import EmbedEditor
from core.ui.theme import brand
from features.starboard.panel import parse_emoji
from features.tickets.buttons import DEFAULT_OPENING, opening_message
from features.tickets.db import MAX_TYPES

if TYPE_CHECKING:
    from core.bot import Bot


Page = Literal["general", "types", "after"]
PAGES: dict[str, tuple[str, str]] = {
    "general": ("General", "🎫"),
    "types": ("Types", "🗂️"),
    "after": ("Auto-close & ratings", "⭐"),
}


def _mention(guild: discord.Guild, channel_id: int | None) -> str:
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel.mention if channel else "not set"


async def status(bot: Bot, guild: discord.Guild) -> str:
    s = await bot.tickets_db.settings(guild.id)
    if not s.enabled:
        return f"{style.OFF} Off"
    open_now = await bot.tickets_db.open_count(guild.id)
    where = f"button in {_mention(guild, s.panel_channel_id)}" if s.panel_message_id else "button not posted yet"
    return f"{style.ON} On · {open_now} open · {where}"


class OpeningMessageEditor(EmbedEditor):
    save_label = "Save message"

    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, draft: EmbedDraft) -> None:
        self.bot, self.guild = bot, guild
        super().__init__(author_id, draft)

    def header(self) -> str:
        return "**Editing the ticket message.** Only you can see this. The Open a ticket button goes under it."

    def render(self) -> dict:
        rendered = super().render()
        if not self.draft.is_empty():
            brand(rendered["embed"], self.guild)
        return rendered

    async def save(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.tickets_db.update_settings(self.guild.id, opening_embed=self.draft.to_dict())
        await _update_posted(self.bot, self.guild, settings)
        self.stop()
        await interaction.response.edit_message(content=f"{style.OK} Ticket message saved.", embed=None, view=None)


async def _update_posted(bot: Bot, guild: discord.Guild, settings) -> None:
    """Redraw the posted ticket message after its text or button label changed."""
    channel = guild.get_channel(settings.panel_channel_id) if settings.panel_channel_id else None
    if not isinstance(channel, discord.abc.Messageable) or not settings.panel_message_id:
        return
    types = await bot.tickets_db.types(guild.id)
    try:
        await channel.get_partial_message(settings.panel_message_id).edit(**opening_message(settings, guild, types))
    except discord.HTTPException:
        pass


def _roles(role_ids) -> str:
    return " ".join(f"<@&{r}>" for r in role_ids)


def rating_lines(overall, staff) -> str:
    if not overall.count:
        return "No ratings yet."
    lines = [f"Overall: **{overall.average:.1f}** ⭐ from {overall.count} rating(s)"]
    lines += [f"<@{row.staff_id}>: **{row.average:.1f}** ⭐ ({row.count})" for row in staff]
    return "\n".join(lines)


class TicketsEditor(SectionEditor):
    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, page: Page = "general") -> None:
        super().__init__(bot, guild, author_id)
        self.page: Page = page
        self.type_id: int | None = None  # the ticket type being changed on the Types page

    async def build(self) -> None:
        s = await self.bot.tickets_db.settings(self.guild.id)
        tabs = self.row(*(
            self.button(label, self._goto(key), emoji=emoji, disabled=key == self.page,
                        style_=discord.ButtonStyle.primary if key == self.page else discord.ButtonStyle.secondary)
            for key, (label, emoji) in PAGES.items()
        ))
        items: list[discord.ui.Item] = [
            self.text("## 🎫 Tickets\nMembers press a button to open a private channel with your staff."),
            tabs, discord.ui.Separator(),
        ]
        items += await getattr(self, f"_{self.page}_page")(s)
        self.add_item(self.container(*items))

    def _goto(self, page: Page):
        async def callback(interaction: discord.Interaction) -> None:
            self.page = page
            await self.rerender(interaction, changed=False)
        return callback

    async def _general_page(self, s) -> list[discord.ui.Item]:
        staff = _roles(s.staff_role_ids) or "none (only members with Manage Channels)"
        post = discord.ui.ChannelSelect(placeholder="Post the ticket button in…", channel_types=[discord.ChannelType.text],
                                        min_values=1, max_values=1)
        post.callback = self.post_button
        return [
            self.toggle("Tickets", s.enabled, self.flip("enabled"), description="When off, the button tells members tickets are closed."),
            self.text(f"**Category for ticket channels** · {_mention(self.guild, s.category_id)}"),
            self.row(self.channel_select("Pick a category", s.category_id, self.set_channel("category_id"),
                                         types=[discord.ChannelType.category])),
            self.text(f"**Staff roles** · {staff}"),
            self.row(self.role_select("Roles that handle tickets", s.staff_role_ids, self.pick_staff, max_values=10)),
            self.text(f"**Transcripts channel** · {_mention(self.guild, s.log_channel_id)}"),
            self.row(self.channel_select("Where closed-ticket transcripts go", s.log_channel_id, self.set_channel("log_channel_id"))),
            self.setting("Limits", f"{s.max_open} open ticket(s) per member · button “{s.button_label}”", "Edit", self.edit_limits),
            self.toggle("Ask for a reason", s.ask_reason, self.flip("ask_reason"), description="A short form before the ticket opens."),
            discord.ui.Separator(),
            self.row(self.button("Edit ticket message", self.edit_message, emoji="✏️", style_=discord.ButtonStyle.primary)),
            self.text("**Ticket button** · "
                      + (f"posted in {_mention(self.guild, s.panel_channel_id)}" if s.panel_message_id else "not posted yet")),
            self.row(post),
        ]

    async def _types_page(self, s) -> list[discord.ui.Item]:
        types = await self.bot.tickets_db.types(self.guild.id)
        lines = [f"{t.emoji or '🎫'} **{t.label}** · staff: {_roles(t.staff_role_ids) or 'server-wide only'} · "
                 f"category: {_mention(self.guild, t.category_id) if t.category_id else 'server-wide'}" for t in types]
        items: list[discord.ui.Item] = [self.text(
            "**Ticket types**\n-# Each type gets its own button on the ticket message. Its staff roles are added to the "
            "server-wide ones, and it can use its own category. With no types, there's one button.\n\n"
            + ("\n".join(lines) if lines else "No types yet.")
        )]
        buttons = [self.button("Add a type", self.add_type, emoji="➕", style_=discord.ButtonStyle.success,
                               disabled=len(types) >= MAX_TYPES)]
        current = next((t for t in types if t.id == self.type_id), None)
        if types:
            items.append(self.row(self.choice_select(
                "Pick a type to change…", [(str(t.id), t.label) for t in types], str(current.id) if current else None,
                self.pick_type)))
        if current:
            buttons += [self.button("Rename", self.rename_type, emoji="✏️"),
                        self.button("Remove", self.remove_type, emoji="🗑️", style_=discord.ButtonStyle.danger)]
            items += [
                self.text(f"**{current.emoji or '🎫'} {current.label}**: its own staff roles and category"),
                self.row(self.role_select("Extra staff roles for this type", current.staff_role_ids, self.set_type_staff,
                                          max_values=10)),
                self.row(self.channel_select("Category for this type (empty = server-wide)", current.category_id,
                                             self.set_type_category, types=[discord.ChannelType.category])),
            ]
        items.append(self.row(*buttons))
        return items

    async def _after_page(self, s) -> list[discord.ui.Item]:
        idle = (f"After {s.idle_close_hours} hour(s) without messages, with a warning {s.idle_warn_hours} hour(s) before"
                if s.idle_close_hours else "Off")
        overall, staff = await self.bot.tickets_db.rating_stats(self.guild.id)
        return [
            self.setting("Close quiet tickets", idle, "Edit", self.edit_idle),
            self.toggle("Ask for a rating", s.ask_rating, self.flip("ask_rating"),
                        description="When a ticket closes, the member gets 1–5 ⭐ buttons in their DM. "
                                    "Ratings are posted in the transcripts channel."),
            discord.ui.Separator(),
            self.text("**Ratings** · by who claimed the ticket\n" + rating_lines(overall, staff)),
        ]

    def flip(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            s = await self.bot.tickets_db.settings(self.guild.id)
            await self.bot.tickets_db.update_settings(self.guild.id, **{field: not getattr(s, field)})
            await self.rerender(interaction)
        return callback

    def set_channel(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            values = interaction.data.get("values") or []
            await self.bot.tickets_db.update_settings(self.guild.id, **{field: int(values[0]) if values else None})
            await self.rerender(interaction)
        return callback

    async def pick_staff(self, interaction: discord.Interaction) -> None:
        roles = [int(v) for v in interaction.data.get("values") or []]
        await self.bot.tickets_db.update_settings(self.guild.id, staff_role_ids=roles)
        await self.rerender(interaction)

    async def edit_limits(self, interaction: discord.Interaction) -> None:
        s = await self.bot.tickets_db.settings(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            try:
                max_open = int(values[0])
            except ValueError:
                raise ValueError("Open tickets per member must be a whole number.") from None
            if not 1 <= max_open <= 10:
                raise ValueError("Open tickets per member must be between 1 and 10.")
            if not values[1]:
                raise ValueError("The button needs a label.")
            settings = await self.bot.tickets_db.update_settings(self.guild.id, max_open=max_open, button_label=values[1])
            await _update_posted(self.bot, self.guild, settings)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Ticket limits", [
            Field("Open tickets per member", str(s.max_open), max_length=2, required=True, description="1–10"),
            Field("Button label", s.button_label, max_length=80, required=True),
        ], save))

    async def edit_message(self, interaction: discord.Interaction) -> None:
        s = await self.bot.tickets_db.settings(self.guild.id)
        editor = OpeningMessageEditor(self.bot, self.guild, interaction.user.id,
                                      EmbedDraft.from_dict(s.opening_embed or DEFAULT_OPENING))
        editor.last_interaction = interaction
        await interaction.response.send_message(**editor.render(), view=editor, ephemeral=True)

    async def post_button(self, interaction: discord.Interaction) -> None:
        channel = self.guild.get_channel(int(interaction.data["values"][0]))
        perms = channel.permissions_for(self.guild.me) if channel else None
        if perms is None or not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=style.fail("I need View Channel, Send Messages and Embed Links in that channel."), ephemeral=True)
            return
        s = await self.bot.tickets_db.settings(self.guild.id)
        message = await channel.send(**opening_message(s, self.guild, await self.bot.tickets_db.types(self.guild.id)))
        old_channel = self.guild.get_channel(s.panel_channel_id) if s.panel_channel_id else None
        if s.panel_message_id and isinstance(old_channel, discord.abc.Messageable) and s.panel_message_id != message.id:
            try:
                await old_channel.get_partial_message(s.panel_message_id).delete()
            except discord.HTTPException:
                pass
        await self.bot.tickets_db.update_settings(self.guild.id, panel_channel_id=channel.id, panel_message_id=message.id)
        await self.rerender(interaction)


    async def edit_idle(self, interaction: discord.Interaction) -> None:
        s = await self.bot.tickets_db.settings(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            close, warn = _hours(values[0], "Close after", 0, 720), _hours(values[1] or "1", "Warning", 1, 719)
            if close and warn >= close:
                raise ValueError("The warning has to come before the ticket closes: make it fewer hours than the close time.")
            fields = {"idle_close_hours": close}
            if close:
                fields["idle_warn_hours"] = warn
            await self.bot.tickets_db.update_settings(self.guild.id, **fields)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Close quiet tickets", [
            Field("Close after how many hours of silence?", str(s.idle_close_hours), max_length=3, required=True,
                  description="0 turns it off. Up to 720 (30 days)."),
            Field("Warn how many hours before closing?", str(s.idle_warn_hours), max_length=3,
                  description="The member is pinged; any message keeps the ticket open."),
        ], save))

    async def add_type(self, interaction: discord.Interaction) -> None:
        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            if not values[0]:
                raise ValueError("The type needs a name.")
            emoji = parse_emoji(values[1], self.guild) if values[1] else None
            added = await self.bot.tickets_db.add_type(self.guild.id, values[0], emoji)
            if added is None:
                raise ValueError(f"A server can have at most {MAX_TYPES} ticket types.")
            self.type_id = added.id
            await self._types_changed()
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Add a ticket type", [
            Field("Name (shown on its button)", max_length=80, required=True, placeholder="Support"),
            Field("Emoji", max_length=64, placeholder="🛟", description="Optional. A normal emoji or one of this server's."),
        ], save))

    async def pick_type(self, interaction: discord.Interaction) -> None:
        self.type_id = int(interaction.data["values"][0])
        await self.rerender(interaction, changed=False)

    async def rename_type(self, interaction: discord.Interaction) -> None:
        current = await self.bot.tickets_db.type(self.guild.id, self.type_id) if self.type_id else None
        if current is None:
            await self.rerender(interaction, changed=False)
            return

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            if not values[0]:
                raise ValueError("The type needs a name.")
            emoji = parse_emoji(values[1], self.guild) if values[1] else None
            await self.bot.tickets_db.update_type(self.guild.id, current.id, label=values[0], emoji=emoji)
            await self._types_changed()
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Rename a ticket type", [
            Field("Name (shown on its button)", current.label, max_length=80, required=True),
            Field("Emoji", current.emoji, max_length=64, description="Optional. Leave empty for 🎫."),
        ], save))

    async def remove_type(self, interaction: discord.Interaction) -> None:
        if self.type_id:
            await self.bot.tickets_db.remove_type(self.guild.id, self.type_id)
        self.type_id = None
        await self._types_changed()
        await self.rerender(interaction)

    async def set_type_staff(self, interaction: discord.Interaction) -> None:
        roles = [int(v) for v in interaction.data.get("values") or []]
        await self.bot.tickets_db.update_type(self.guild.id, self.type_id, staff_role_ids=roles)
        await self.rerender(interaction)

    async def set_type_category(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.tickets_db.update_type(self.guild.id, self.type_id, category_id=int(values[0]) if values else None)
        await self.rerender(interaction)

    async def _types_changed(self) -> None:
        await _update_posted(self.bot, self.guild, await self.bot.tickets_db.settings(self.guild.id))


def _hours(text: str, name: str, low: int, high: int) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number of hours.") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high} hours.")
    return value


SECTION = PanelSection(key="tickets", label="Tickets", emoji="🎫", order=35, status=status, open=TicketsEditor.open)


async def check(bot: Bot, guild: discord.Guild) -> tuple[bool, str] | None:
    s = await bot.tickets_db.settings(guild.id)
    if not s.enabled:
        return None  # only a to-do once tickets are turned on
    return bool(s.panel_message_id), "The ticket button is posted (🎫 Tickets)"
