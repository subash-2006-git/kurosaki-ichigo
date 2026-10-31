"""Control panel section for tickets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from core.ui.embed_draft import EmbedDraft
from core.ui.embed_editor import EmbedEditor
from core.ui.theme import brand
from features.tickets.buttons import DEFAULT_OPENING, opening_message

if TYPE_CHECKING:
    from core.bot import Bot


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
    try:
        await channel.get_partial_message(settings.panel_message_id).edit(**opening_message(settings, guild))
    except discord.HTTPException:
        pass


class TicketsEditor(SectionEditor):
    async def build(self) -> None:
        s = await self.bot.tickets_db.settings(self.guild.id)
        staff = " ".join(f"<@&{r}>" for r in s.staff_role_ids) or "none (only members with Manage Channels)"
        post = discord.ui.ChannelSelect(placeholder="Post the ticket button in…", channel_types=[discord.ChannelType.text],
                                        min_values=1, max_values=1)
        post.callback = self.post_button
        self.add_item(self.container(
            self.text("## 🎫 Tickets\nMembers press a button to open a private channel with your staff."),
            discord.ui.Separator(),
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
            self.text(f"**Ticket button** · {'posted in ' + _mention(self.guild, s.panel_channel_id) if s.panel_message_id else 'not posted yet'}"),
            self.row(post),
        ))

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
        message = await channel.send(**opening_message(s, self.guild))
        old_channel = self.guild.get_channel(s.panel_channel_id) if s.panel_channel_id else None
        if s.panel_message_id and isinstance(old_channel, discord.abc.Messageable) and s.panel_message_id != message.id:
            try:
                await old_channel.get_partial_message(s.panel_message_id).delete()
            except discord.HTTPException:
                pass
        await self.bot.tickets_db.update_settings(self.guild.id, panel_channel_id=channel.id, panel_message_id=message.id)
        await self.rerender(interaction)


SECTION = PanelSection(key="tickets", label="Tickets", emoji="🎫", order=35, status=status, open=TicketsEditor.open)


async def check(bot: Bot, guild: discord.Guild) -> tuple[bool, str] | None:
    s = await bot.tickets_db.settings(guild.id)
    if not s.enabled:
        return None  # only a to-do once tickets are turned on
    return bool(s.panel_message_id), "The ticket button is posted (🎫 Tickets)"
