import copy
from collections.abc import Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from features.nickname.services.embed_draft import (
    AUTHOR_LIMIT,
    FIELD_COUNT_LIMIT,
    FIELD_NAME_LIMIT,
    FIELD_VALUE_LIMIT,
    FOOTER_LIMIT,
    MENTION_LIMIT,
    TITLE_LIMIT,
    DraftError,
    EmbedDraft,
    allowed_mentions,
    parse_colour,
    parse_yes_no,
    validate_url,
)

if TYPE_CHECKING:
    from core.bot import Bot as NicknameBot

IDLE_TIMEOUT = 600
MODAL_TEXT_LIMIT = 4000  # Discord's cap for a single modal text input
SENDABLE_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]

PLACEHOLDER_EMBED = discord.Embed(
    description="*This embed is empty. Use the buttons below to add a title, text, fields, or images.*",
    colour=discord.Colour.greyple(),
)

Change = Callable[[EmbedDraft], None]


def text_input(label: str, default: str | None = None, *, max_length: int = 512,
               paragraph: bool = False, required: bool = False, placeholder: str | None = None) -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label=label,
        default=default,
        max_length=min(max_length, MODAL_TEXT_LIMIT),
        style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
        required=required,
        placeholder=placeholder,
    )


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def channel_problem(channel: discord.abc.GuildChannel, sender: discord.Member, me: discord.Member) -> str | None:
    """Return why the embed can't be posted in this channel, if anything stops it."""
    bot = channel.permissions_for(me)
    can_send = bot.send_messages_in_threads if isinstance(channel, discord.Thread) else bot.send_messages
    missing = [name for name, ok in (
        ("View Channel", bot.view_channel), ("Send Messages", can_send), ("Embed Links", bot.embed_links),
    ) if not ok]
    if missing:
        return f"I need {', '.join(f'**{m}**' for m in missing)} in {channel.mention} to post there."

    user = channel.permissions_for(sender)
    if not (user.view_channel and user.send_messages):
        return f"You can't send messages in {channel.mention}, so I won't post there for you."
    return None


class FormModal(discord.ui.Modal):
    """A pop-up form whose answers are applied to the draft by `change`."""

    def __init__(self, builder: "BuilderView", title: str, inputs: list[discord.ui.TextInput],
                 change: Callable[[EmbedDraft, list[str]], None]):
        super().__init__(title=title, timeout=IDLE_TIMEOUT)
        self.builder = builder
        self.inputs = inputs
        self.change = change
        for item in inputs:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        values = [str(item.value).strip() for item in self.inputs]
        await self.builder.apply(interaction, lambda draft: self.change(draft, values))


class BuilderView(discord.ui.View):
    """The private builder message: a live preview plus controls, on two screens."""

    def __init__(self, author_id: int, channel: discord.abc.GuildChannel):
        super().__init__(timeout=IDLE_TIMEOUT)
        self.author_id = author_id
        self.channel = channel
        self.draft = EmbedDraft()
        self.last_interaction: discord.Interaction | None = None
        self.on_ping_screen = False

        def button(label: str, row: int, callback, style=discord.ButtonStyle.secondary) -> discord.ui.Button:
            item = discord.ui.Button(label=label, style=style, row=row)
            item.callback = callback
            return item

        # Main screen
        self.remove_field_button = button("Remove field", 1, self.remove_field)
        self.add_field_button = button("Add field", 1, self.add_field)
        self.pings_button = button("Message & pings", 1, self.show_pings)
        self.send_button = button("Send embed", 3, self.send, discord.ButtonStyle.success)
        self.channel_select = discord.ui.ChannelSelect(
            channel_types=SENDABLE_CHANNELS, placeholder="Channel to send to", row=2,
            default_values=[discord.SelectDefaultValue(id=channel.id, type=discord.SelectDefaultValueType.channel)]
            if channel.type in SENDABLE_CHANNELS else [],
        )
        self.channel_select.callback = self.pick_channel
        self.main_items = [
            button("Title & text", 0, self.edit_title, discord.ButtonStyle.primary),
            button("Colour", 0, self.edit_colour),
            button("Author", 0, self.edit_author),
            button("Footer", 0, self.edit_footer),
            button("Images", 1, self.edit_images),
            self.add_field_button,
            self.remove_field_button,
            self.pings_button,
            self.channel_select,
            self.send_button,
            button("Cancel", 3, self.cancel, discord.ButtonStyle.danger),
        ]

        # Message & pings screen
        self.mention_select = discord.ui.MentionableSelect(
            placeholder="Roles or members to ping", min_values=0, max_values=MENTION_LIMIT, row=0,
        )
        self.mention_select.callback = self.pick_mentions
        self.everyone_button = button("@everyone", 1, self.toggle_everyone)
        self.here_button = button("@here", 1, self.toggle_here)
        self.ping_items = [
            self.mention_select,
            self.everyone_button,
            self.here_button,
            button("Edit message text", 2, self.edit_message, discord.ButtonStyle.primary),
            button("Back", 2, self.show_main),
        ]
        self._show(self.main_items)

    # --- rendering ------------------------------------------------------------

    def _show(self, items: list[discord.ui.Item]) -> None:
        self.clear_items()
        for item in items:
            self.add_item(item)

    def _sync(self) -> None:
        draft = self.draft
        self.send_button.disabled = draft.is_empty()
        self.remove_field_button.disabled = not draft.fields
        self.add_field_button.disabled = len(draft.fields) >= FIELD_COUNT_LIMIT
        self.pings_button.label = "Message & pings ✓" if draft.message_content() else "Message & pings"
        for item, on in ((self.everyone_button, draft.ping_everyone), (self.here_button, draft.ping_here)):
            item.style = discord.ButtonStyle.success if on else discord.ButtonStyle.secondary
        self.everyone_button.label = f"@everyone: {'on' if draft.ping_everyone else 'off'}"
        self.here_button.label = f"@here: {'on' if draft.ping_here else 'off'}"
        self.mention_select.default_values = (
            [discord.SelectDefaultValue(id=r, type=discord.SelectDefaultValueType.role) for r in draft.mention_role_ids]
            + [discord.SelectDefaultValue(id=u, type=discord.SelectDefaultValueType.user) for u in draft.mention_user_ids]
        )

    def render(self) -> dict:
        self._sync()
        lines = [f"**Embed builder:** only you can see this. It will be sent to {self.channel.mention}."]
        if self.on_ping_screen:
            lines.append("Pick roles or members to ping, turn @everyone or @here on, and set the message text. "
                         "Nobody is pinged while you build.")
        if self.draft.fields:
            numbered = ", ".join(f"{i}. {f.name[:30]}" for i, f in enumerate(self.draft.fields, 1))
            lines.append(f"Fields: {numbered}")
        if content := self.draft.message_content():
            lines.append(f"**Message above the embed:**\n{content}")
        text = "\n".join(lines)
        if len(text) > 2000:
            text = text[:1999] + "…"
        embed = PLACEHOLDER_EMBED if self.draft.is_empty() else self.draft.to_embed()
        return {"content": text, "embed": embed}

    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(**self.render(), view=self)

    async def apply(self, interaction: discord.Interaction, change: Change) -> None:
        """Change the draft, keeping the old version if the result is invalid."""
        self.last_interaction = interaction
        backup = copy.deepcopy(self.draft)
        try:
            change(self.draft)
            self.draft.check()
        except DraftError as exc:
            self.draft = backup
            await interaction.response.send_message(f"⚠ {exc}", ephemeral=True)
            return
        try:
            await self.refresh(interaction)
        except discord.HTTPException as exc:
            # Usually an image link Discord can't accept.
            self.draft = backup
            await interaction.response.send_message(f"⚠ Discord rejected that change: {exc.text}", ephemeral=True)

    # --- session --------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the person who opened this builder can use it.", ephemeral=True)
            return False
        self.last_interaction = interaction
        return True

    async def on_timeout(self) -> None:
        if self.last_interaction is None:
            return
        try:
            await self.last_interaction.edit_original_response(
                content="This embed builder closed after 10 minutes without use. Run `/embed create` to start again.",
                embed=None, view=None,
            )
        except discord.HTTPException:
            pass

    # --- main screen ----------------------------------------------------------

    async def edit_title(self, interaction: discord.Interaction):
        d = self.draft

        def change(draft: EmbedDraft, v: list[str]):
            draft.title, draft.url, draft.description = v[0] or None, validate_url(v[1], "The title link"), v[2] or None

        await interaction.response.send_modal(FormModal(self, "Title & text", [
            text_input("Title", d.title, max_length=TITLE_LIMIT),
            text_input("Title link (optional)", d.url, placeholder="https://…"),
            text_input("Description", d.description, max_length=MODAL_TEXT_LIMIT, paragraph=True),
        ], change))

    async def edit_colour(self, interaction: discord.Interaction):
        current = f"#{self.draft.colour:06X}" if self.draft.colour is not None else None

        def change(draft: EmbedDraft, v: list[str]):
            draft.colour = parse_colour(v[0])

        await interaction.response.send_modal(FormModal(self, "Colour", [
            text_input("Colour", current, max_length=20, placeholder="#5865F2, or a name like blurple, red, gold"),
        ], change))

    async def edit_author(self, interaction: discord.Interaction):
        d = self.draft

        def change(draft: EmbedDraft, v: list[str]):
            draft.author_name = v[0] or None
            draft.author_icon = validate_url(v[1], "The author icon")
            draft.author_url = validate_url(v[2], "The author link")

        await interaction.response.send_modal(FormModal(self, "Author", [
            text_input("Author name", d.author_name, max_length=AUTHOR_LIMIT),
            text_input("Icon URL (optional)", d.author_icon, placeholder="https://…/icon.png"),
            text_input("Link (optional)", d.author_url, placeholder="https://…"),
        ], change))

    async def edit_footer(self, interaction: discord.Interaction):
        d = self.draft

        def change(draft: EmbedDraft, v: list[str]):
            draft.footer_text = v[0] or None
            draft.footer_icon = validate_url(v[1], "The footer icon")
            draft.timestamp = parse_yes_no(v[2])

        await interaction.response.send_modal(FormModal(self, "Footer", [
            text_input("Footer text", d.footer_text, max_length=FOOTER_LIMIT, paragraph=True),
            text_input("Icon URL (optional)", d.footer_icon, placeholder="https://…/icon.png"),
            text_input("Show the time it was sent? (yes/no)", yes_no(d.timestamp), max_length=5),
        ], change))

    async def edit_images(self, interaction: discord.Interaction):
        d = self.draft

        def change(draft: EmbedDraft, v: list[str]):
            draft.image_url = validate_url(v[0], "The large image")
            draft.thumbnail_url = validate_url(v[1], "The thumbnail")

        await interaction.response.send_modal(FormModal(self, "Images", [
            text_input("Large image URL", d.image_url, placeholder="https://…/banner.png"),
            text_input("Thumbnail URL (small, top right)", d.thumbnail_url, placeholder="https://…/logo.png"),
        ], change))

    async def add_field(self, interaction: discord.Interaction):
        def change(draft: EmbedDraft, v: list[str]):
            draft.add_field(v[0], v[1], parse_yes_no(v[2]))

        await interaction.response.send_modal(FormModal(self, f"Add field {len(self.draft.fields) + 1}", [
            text_input("Name", max_length=FIELD_NAME_LIMIT, required=True),
            text_input("Value", max_length=FIELD_VALUE_LIMIT, paragraph=True, required=True),
            text_input("Show side by side with other fields? (yes/no)", "no", max_length=5),
        ], change))

    async def remove_field(self, interaction: discord.Interaction):
        def change(draft: EmbedDraft, v: list[str]):
            draft.remove_field(v[0])

        default = str(len(self.draft.fields)) if len(self.draft.fields) == 1 else None
        await interaction.response.send_modal(FormModal(self, "Remove field", [
            text_input("Field number", default, max_length=2, required=True,
                       placeholder=f"1 to {len(self.draft.fields)}"),
        ], change))

    async def pick_channel(self, interaction: discord.Interaction):
        picked = self.channel_select.values[0]
        self.channel = interaction.guild.get_channel(picked.id) or self.channel
        await self.refresh(interaction)

    async def send(self, interaction: discord.Interaction):
        channel = self.channel
        guild = interaction.guild
        try:
            if self.draft.is_empty():
                raise DraftError("Add something to the embed first.")
            self.draft.check()
            if problem := channel_problem(channel, interaction.user, guild.me):
                raise DraftError(problem)
            mentions = allowed_mentions(
                self.draft,
                {r: guild.get_role(r) for r in self.draft.mention_role_ids},
                sender_can_mention_everyone=channel.permissions_for(interaction.user).mention_everyone,
                bot_can_mention_everyone=channel.permissions_for(guild.me).mention_everyone,
            )
        except DraftError as exc:
            await interaction.response.send_message(f"⚠ {exc}", ephemeral=True)
            return

        try:
            message = await channel.send(
                content=self.draft.message_content(), embed=self.draft.to_embed(), allowed_mentions=mentions
            )
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"⚠ Discord didn't accept the embed: {exc.text or exc.status}. Your draft is still here.",
                ephemeral=True,
            )
            return

        self.stop()
        await interaction.response.edit_message(
            content=f"✅ Sent to {channel.mention}: {message.jump_url}", embed=None, view=None
        )

    async def cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(content="Embed builder closed. Nothing was sent.", embed=None, view=None)

    # --- message & pings screen -----------------------------------------------

    async def show_pings(self, interaction: discord.Interaction):
        self.on_ping_screen = True
        self._show(self.ping_items)
        await self.refresh(interaction)

    async def show_main(self, interaction: discord.Interaction):
        self.on_ping_screen = False
        self._show(self.main_items)
        await self.refresh(interaction)

    async def pick_mentions(self, interaction: discord.Interaction):
        values = self.mention_select.values

        def change(draft: EmbedDraft):
            draft.mention_role_ids = [v.id for v in values if isinstance(v, discord.Role)]
            draft.mention_user_ids = [v.id for v in values if not isinstance(v, discord.Role)]

        await self.apply(interaction, change)

    async def toggle_everyone(self, interaction: discord.Interaction):
        await self.apply(interaction, lambda d: setattr(d, "ping_everyone", not d.ping_everyone))

    async def toggle_here(self, interaction: discord.Interaction):
        await self.apply(interaction, lambda d: setattr(d, "ping_here", not d.ping_here))

    async def edit_message(self, interaction: discord.Interaction):
        def change(draft: EmbedDraft, v: list[str]):
            draft.message = v[0] or None

        await interaction.response.send_modal(FormModal(self, "Message above the embed", [
            text_input("Message text", self.draft.message, max_length=2000, paragraph=True,
                       placeholder="Shown above the embed. Pings are chosen on the previous screen."),
        ], change))


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class EmbedCommands(commands.GroupCog, group_name="embed", group_description="Create and send embeds"):
    def __init__(self, bot: "NicknameBot"):
        self.bot = bot
        super().__init__()

    @app_commands.command(name="create", description="Build an embed with a live preview, then send it")
    @app_commands.describe(channel="Where to send it (defaults to this channel; you can change it in the builder)")
    async def create(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        view = BuilderView(interaction.user.id, channel or interaction.channel)
        view.last_interaction = interaction
        await interaction.response.send_message(**view.render(), view=view, ephemeral=True)


async def setup(bot: "NicknameBot"):
    await bot.add_cog(EmbedCommands(bot))
