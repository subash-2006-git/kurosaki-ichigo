"""Control panel sections for the community feature: Welcome & Goodbye, and Leveling."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Literal

import discord

from core.panel.editor import Field, Form, SectionEditor
from core.panel.service import PanelSection
from core.ui import style
from core.ui.embed_draft import EmbedDraft
from core.ui.embed_editor import EmbedEditor
from features.community.models import RoleMode
from features.community.services.greeting import MAX_CONTENT, build_greeting, greeting_settings
from features.community.services.media import (
    MAX_IMAGE_BYTES,
    UPLOAD_PREFIX,
    InvalidMedia,
    download_image,
    inspect_image,
)
from features.community.services.messages import (
    DEFAULT_EMBEDS,
    MAX_LINK_BUTTONS,
    MessageKind,
    buttons_text,
    fill,
    is_default,
    link_buttons,
    parse_buttons,
    stored_draft,
)
from features.community.services.xp_rules import boost_multiplier
from features.community.utils.checks import role_problem
from features.community.utils.placeholders import help_text
from features.moderation.utils.time import parse_duration

if TYPE_CHECKING:
    from core.bot import Bot

GreetingKind = Literal["welcome", "goodbye"]
TITLES = {"welcome": "Welcome", "goodbye": "Goodbye", "levelup": "Level-up"}
WHEN = {"welcome": "someone joins", "goodbye": "someone leaves"}


def _channel(guild: discord.Guild, channel_id: int | None) -> str:
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel.mention if channel else "no channel"


# ---- status lines on the posted panel ------------------------------------------------


async def welcome_status(bot: Bot, guild: discord.Guild) -> str:
    config = await bot.guild_config.get(guild.id)
    parts = []
    for kind in ("welcome", "goodbye"):
        s = greeting_settings(config, kind)
        parts.append(f"{style.ON if s.enabled else style.OFF} {TITLES[kind]}"
                     + (f" in {_channel(guild, s.channel_id)}" if s.enabled else ""))
    return " · ".join(parts)


async def leveling_status(bot: Bot, guild: discord.Guild) -> str:
    config = await bot.guild_config.get(guild.id)
    if not config.leveling_enabled:
        return f"{style.OFF} XP is off"
    if not config.levelup_enabled:
        where = "level-ups not announced"
    elif config.levelup_channel_id:
        where = f"level-ups in {_channel(guild, config.levelup_channel_id)}"
    else:
        where = "level-ups where members chat"
    roles = len(await bot.roles.milestones(guild.id))
    line = f"{style.ON} XP on · {where} · {roles} level role{'s' if roles != 1 else ''}"
    if boost_multiplier(config) > 1:
        line += f" · ⚡ {boost_text(config)}"
    return line


def boost_text(config) -> str:
    multiplier = boost_multiplier(config)
    if multiplier == 1:
        return "No boost running. Start one for double (or more) XP for a while."
    until = config.xp_boost_until
    return f"{multiplier}× XP until {discord.utils.format_dt(until, 'f')} ({discord.utils.format_dt(until, 'R')})"


# ---- the embed editor for stored messages ---------------------------------------------


class MessageEmbedEditor(EmbedEditor):
    """Edit the stored welcome/goodbye/level-up embed. The preview fills placeholders with you."""

    save_label = "Save message"

    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, kind: MessageKind, draft: EmbedDraft) -> None:
        self.bot, self.guild, self.kind = bot, guild, kind
        super().__init__(author_id, draft)

    def extra_items(self) -> list[discord.ui.Item]:
        return [
            self.button("Placeholders", 3, self.show_placeholders),
            self.button("Reset to default", 3, self.reset),
        ]

    def header(self) -> str:
        sample = "Level 5 and you" if self.kind == "levelup" else "you"
        return (f"**Editing the {TITLES[self.kind].lower()} message.** Only you can see this. "
                f"Placeholders like `{{user}}` are filled in with {sample} for this preview.")

    def preview(self, draft: EmbedDraft) -> EmbedDraft:
        member = self.guild.get_member(self.author_id)
        if member is None:
            return draft
        return fill(draft, member, level=5 if self.kind == "levelup" else None)

    def render(self) -> dict:
        rendered = super().render()
        if not self.preview(self.draft).is_empty():
            self.bot.theme.brand(rendered["embed"], self.guild)
        return rendered

    async def save(self, interaction: discord.Interaction) -> None:
        await self.bot.guild_config.update(
            self.guild.id, **{f"{self.kind}_embed": self.draft.to_dict(), f"{self.kind}_use_embed": True}
        )
        self.stop()
        await interaction.response.edit_message(
            content=f"{style.OK} {TITLES[self.kind]} message saved. It's used from the next {self._event()} on.",
            embed=None, view=None,
        )
        await self.bot.panel.refresh(self.guild)

    def _event(self) -> str:
        return {"welcome": "join", "goodbye": "leave", "levelup": "level-up"}[self.kind]

    async def reset(self, interaction: discord.Interaction) -> None:
        self.draft = EmbedDraft.from_dict(DEFAULT_EMBEDS[self.kind])
        await self.bot.guild_config.update(self.guild.id, **{f"{self.kind}_embed": None})
        await self.refresh(interaction)

    async def show_placeholders(self, interaction: discord.Interaction) -> None:
        embed = style.info("Placeholders", help_text(include_level=self.kind == "levelup"))
        embed.set_footer(text="Use them in any text or image link of the embed.")
        self.bot.theme.brand(embed, self.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def open_message_editor(interaction: discord.Interaction, kind: MessageKind) -> None:
    bot = interaction.client
    config = await bot.guild_config.get(interaction.guild_id)
    editor = MessageEmbedEditor(bot, interaction.guild, interaction.user.id, kind, stored_draft(config, kind))
    editor.last_interaction = interaction
    await interaction.response.send_message(**editor.render(), view=editor, ephemeral=True)


def links_form(bot: Bot, guild: discord.Guild, kind: MessageKind, current: list[dict], after) -> Form:
    async def save(interaction: discord.Interaction, values: list[str]) -> None:
        buttons = parse_buttons(values[0])
        await bot.guild_config.update(guild.id, **{f"{kind}_buttons": buttons})
        await after(interaction)

    return Form(f"{TITLES[kind]} link buttons", [
        Field("Buttons, one per line", buttons_text(current), paragraph=True, max_length=1500,
              placeholder="📜 Rules | https://example.com/rules\nWebsite | https://example.com",
              description=f"Label | link. Up to {MAX_LINK_BUTTONS}. Start a label with an emoji if you like."),
    ], save)


# ---- Welcome & Goodbye ----------------------------------------------------------------


class BackgroundForm(discord.ui.Modal):
    def __init__(self, editor: WelcomeEditor) -> None:
        super().__init__(title=f"{TITLES[editor.kind]} card background", timeout=600)
        self.editor = editor
        self.upload = discord.ui.FileUpload(required=False, max_values=1)
        self.link = discord.ui.TextInput(required=False, placeholder="https://… (a direct image or GIF link)", max_length=500)
        self.add_item(discord.ui.Label(text="Upload an image or GIF", description="Max 8 MB.", component=self.upload))
        self.add_item(discord.ui.Label(text="…or paste a link", component=self.link))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        editor = self.editor
        bot = editor.bot
        files = self.upload.values
        url = str(self.link.value or "").strip()
        if not files and not url:
            await interaction.response.send_message(embed=style.warn("Upload a file or paste a link."), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            if files:
                file = files[0]
                if file.size > MAX_IMAGE_BYTES:
                    raise InvalidMedia(f"That file is too large ({file.size / 1024 / 1024:.1f} MB, max 8 MB).")
                data, source = await file.read(), f"{UPLOAD_PREFIX}{file.filename}"
            else:
                data, source = await download_image(bot.http_session, url), url
            await asyncio.to_thread(inspect_image, data)
        except InvalidMedia as exc:
            await interaction.followup.send(embed=style.fail(str(exc)), ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send(embed=style.fail("I couldn't read that file. Try again."), ephemeral=True)
            return
        await bot.media.save(editor.guild.id, editor.kind, data)
        await bot.guild_config.update(editor.guild.id, **{f"{editor.kind}_gif_url": source})
        await editor.rerender(interaction)


class WelcomeEditor(SectionEditor):
    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, kind: GreetingKind = "welcome") -> None:
        super().__init__(bot, guild, author_id)
        self.kind: GreetingKind = kind

    async def build(self) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        kind, s = self.kind, greeting_settings(config, self.kind)
        links = link_buttons(config, kind)
        tabs = self.row(*(
            self.button(TITLES[k], self._switch(k), style_=discord.ButtonStyle.primary if k == kind else discord.ButtonStyle.secondary,
                        disabled=k == kind, emoji="👋" if k == "welcome" else "🚪")
            for k in ("welcome", "goodbye")
        ))
        items: list[discord.ui.Item] = [
            self.text("## 👋 Welcome & Goodbye"),
            tabs,
            discord.ui.Separator(),
            self.toggle(f"{TITLES[kind]} messages", s.enabled, self._flip(f"{kind}_enabled"),
                        description=f"Posted when {WHEN[kind]}."),
            self.text(f"**Channel** · {_channel(self.guild, s.channel_id)}"),
            self.row(self.channel_select(f"Pick the {kind} channel", s.channel_id, self.pick_channel)),
            self.toggle("Embed style", s.use_embed, self._flip(f"{kind}_use_embed"),
                        description="Off sends a plain text message instead."),
            self.toggle("Image card", s.card_enabled, self._flip(f"{kind}_card_enabled"),
                        description="A picture with their avatar and name, shown in the message."),
            self.setting("Card background", "Custom image" if s.background_source else "Default gradient",
                         "Change", self.change_background),
            self.setting("Link buttons", ", ".join(b["label"] for b in links) or "None",
                         "Edit", self.edit_links),
        ]
        if kind == "goodbye":
            items += [
                self.toggle("DM the member", config.goodbye_dm_enabled, self._flip("goodbye_dm_enabled"),
                            description="Best effort: only works if they still share a server with the bot."),
                self.setting("DM text", config.goodbye_dm_text[:80], "Edit", self.edit_dm),
            ]
        message_note = ("default" if is_default(config, kind) else "customised") if s.use_embed else "plain text"
        items += [
            discord.ui.Separator(),
            self.row(
                self.button(f"Edit message ({message_note})", self.edit_message, emoji="✏️", style_=discord.ButtonStyle.primary),
                self.button("Preview", self.preview, emoji="👀"),
                self.button("Remove background", self.remove_background, style_=discord.ButtonStyle.danger,
                            disabled=not s.background_source),
            ),
        ]
        self.add_item(self.container(*items))

    def _switch(self, kind: GreetingKind):
        async def callback(interaction: discord.Interaction) -> None:
            self.kind = kind
            await self.rerender(interaction, changed=False)
        return callback

    def _flip(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            config = await self.bot.guild_config.get(self.guild.id)
            await self.bot.guild_config.update(self.guild.id, **{field: not getattr(config, field)})
            await self.rerender(interaction)
        return callback

    async def pick_channel(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.guild_config.update(self.guild.id, **{f"{self.kind}_channel_id": int(values[0]) if values else None})
        await self.rerender(interaction)

    async def change_background(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BackgroundForm(self))

    async def remove_background(self, interaction: discord.Interaction) -> None:
        await self.bot.media.delete(self.guild.id, self.kind)
        await self.bot.guild_config.update(self.guild.id, **{f"{self.kind}_gif_url": None})
        await self.rerender(interaction)

    async def edit_links(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        await interaction.response.send_modal(
            links_form(self.bot, self.guild, self.kind, link_buttons(config, self.kind), self.rerender)
        )

    async def edit_dm(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            if not values[0]:
                raise ValueError("The DM text can't be empty.")
            await self.bot.guild_config.update(self.guild.id, goodbye_dm_text=values[0])
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Goodbye DM text", [
            Field("DM text", config.goodbye_dm_text, paragraph=True, max_length=1000, required=True,
                  description="Placeholders like {server} and {username} work here."),
        ], save))

    async def edit_message(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        if getattr(config, f"{self.kind}_use_embed"):
            await open_message_editor(interaction, self.kind)
            return

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            if not values[0]:
                raise ValueError("The message can't be empty.")
            await self.bot.guild_config.update(self.guild.id, **{f"{self.kind}_text": values[0]})
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form(f"{TITLES[self.kind]} text", [
            Field("Message", greeting_settings(config, self.kind).text, paragraph=True, max_length=1000, required=True,
                  description="Plain text. Placeholders like {user} and {server} work here."),
        ], save))

    async def preview(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        config = await self.bot.guild_config.get(self.guild.id)
        message = await build_greeting(
            interaction.user, self.kind, config, self.bot.media, self.bot.http_session,
            brand=lambda embed: self.bot.theme.brand(embed, self.guild),
        )
        header = f"-# Preview with you as the member · posts in {_channel(self.guild, greeting_settings(config, self.kind).channel_id)}"
        message.content = "\n".join(part for part in (header, message.content) if part)[:MAX_CONTENT]
        await interaction.followup.send(**message.send_kwargs(), ephemeral=True)


# ---- Leveling ------------------------------------------------------------------------

LevelPage = Literal["general", "xp", "roles"]
PAGE_LABELS = {"general": ("General", "📈"), "xp": ("XP rules", "⚡"), "roles": ("Level roles", "🏅")}


class LevelingEditor(SectionEditor):
    def __init__(self, bot: Bot, guild: discord.Guild, author_id: int, page: LevelPage = "general") -> None:
        super().__init__(bot, guild, author_id)
        self.page: LevelPage = page

    async def build(self) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        tabs = self.row(*(
            self.button(label, self._goto(key), emoji=emoji, disabled=key == self.page,
                        style_=discord.ButtonStyle.primary if key == self.page else discord.ButtonStyle.secondary)
            for key, (label, emoji) in PAGE_LABELS.items()
        ))
        items: list[discord.ui.Item] = [self.text("## 📈 Leveling"), tabs, discord.ui.Separator()]
        items += await getattr(self, f"_{self.page}_page")(config)
        self.add_item(self.container(*items))

    async def _general_page(self, config) -> list[discord.ui.Item]:
        links = link_buttons(config, "levelup")
        note = ("default" if is_default(config, "levelup") else "customised") if config.levelup_use_embed else "plain text"
        where = _channel(self.guild, config.levelup_channel_id) if config.levelup_channel_id else "where the member was chatting"
        return [
            self.toggle("Leveling", config.leveling_enabled, self._flip("leveling_enabled"),
                        description="Members earn XP for chatting and time in voice."),
            self.toggle("Level-up announcements", config.levelup_enabled, self._flip("levelup_enabled")),
            self.text(f"**Announcement channel** · {where}"),
            self.row(self.channel_select("Pick a channel (clear it for 'where they chat')",
                                         config.levelup_channel_id, self.pick_channel)),
            self.toggle("Embed style", config.levelup_use_embed, self._flip("levelup_use_embed"),
                        description="Off sends a plain text message instead."),
            self.setting("Link buttons", ", ".join(b["label"] for b in links) or "None (📊 My rank and 🏆 Leaderboard are always there)",
                         "Edit", self.edit_links),
            discord.ui.Separator(),
            self.row(self.button(f"Edit level-up message ({note})", self.edit_message, emoji="✏️",
                                 style_=discord.ButtonStyle.primary),
                     self.button("Preview", self.preview, emoji="👀")),
        ]

    async def _xp_page(self, config) -> list[discord.ui.Item]:
        return [
            self.setting("Message XP",
                         f"{config.message_xp_min}–{config.message_xp_max} XP per message, at most every {config.message_xp_cooldown}s",
                         "Edit", self.edit_message_xp),
            self.setting("Voice XP",
                         f"{config.voice_xp_amount} XP every {config.voice_xp_interval}s, after {config.voice_min_session}s in voice",
                         "Edit", self.edit_voice_xp),
            self.toggle("Only with others in voice", config.voice_require_others, self._flip("voice_require_others"),
                        description="No XP when alone in a channel."),
            self.toggle("No XP while muted", config.voice_ignore_muted, self._flip("voice_ignore_muted")),
            self.toggle("No XP while deafened", config.voice_ignore_deafened, self._flip("voice_ignore_deafened")),
            discord.ui.Separator(),
            self.setting("⚡ XP boost", boost_text(config), "Start" if boost_multiplier(config) == 1 else "Change",
                         self.edit_boost),
            *([self.row(self.button("End the boost now", self.end_boost, style_=discord.ButtonStyle.danger))]
              if boost_multiplier(config) > 1 else []),
            self.text("**No-XP channels**\n-# Messages and voice time here earn nothing. Picking a category covers "
                      "every channel in it."),
            self.row(self._no_xp_select(config)),
        ]

    def _no_xp_select(self, config) -> discord.ui.ChannelSelect:
        select = discord.ui.ChannelSelect(
            placeholder="Channels or categories that earn no XP", min_values=0, max_values=25,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.voice,
                           discord.ChannelType.stage_voice, discord.ChannelType.forum, discord.ChannelType.category],
            default_values=[discord.SelectDefaultValue(id=c, type=discord.SelectDefaultValueType.channel)
                            for c in list(dict.fromkeys(config.no_xp_channel_ids or []))[:25]],
        )
        select.callback = self.pick_no_xp
        return select

    async def pick_no_xp(self, interaction: discord.Interaction) -> None:
        channels = [int(v) for v in interaction.data.get("values") or []]
        await self.bot.guild_config.update(self.guild.id, no_xp_channel_ids=channels)
        await self.rerender(interaction)

    async def edit_boost(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            multiplier = _number(values[0], "Multiplier", 2, 5)
            duration = parse_duration(values[1])
            if duration is None or duration > timedelta(days=30):
                raise ValueError("Use a duration like 2h, 1d or 3d (up to 30 days).")
            until = discord.utils.utcnow() + duration
            await self.bot.guild_config.update(self.guild.id, xp_boost_multiplier=multiplier, xp_boost_until=until)
            await self.rerender(modal_interaction)

        current = str(config.xp_boost_multiplier) if boost_multiplier(config) > 1 else "2"
        await interaction.response.send_modal(Form("XP boost", [
            Field("Multiplier", current, max_length=1, required=True, description="2–5. 2 means double XP."),
            Field("How long", "2d", max_length=10, required=True, description="e.g. 6h, 2d, 1w (up to 30 days)."),
        ], save))

    async def end_boost(self, interaction: discord.Interaction) -> None:
        await self.bot.guild_config.update(self.guild.id, xp_boost_multiplier=1, xp_boost_until=None)
        await self.rerender(interaction)

    async def _roles_page(self, config) -> list[discord.ui.Item]:
        milestones = await self.bot.roles.milestones(self.guild.id)
        lines = []
        for m in milestones:
            role = self.guild.get_role(m.role_id) if m.role_id else None
            target = role.mention if role else f"*a new “Level {m.level}” role, created on first use*"
            lines.append(f"Level **{m.level}** → {target}")
        items: list[discord.ui.Item] = [
            self.text("\n".join(lines) if lines else "No level roles yet. Add one to reward members as they level up."),
            self.row(self.choice_select(
                "Which level roles members keep",
                [(RoleMode.HIGHEST.value, "Only the highest one reached"), (RoleMode.KEEP_ALL.value, "Every one reached")],
                config.level_role_mode, self.pick_mode,
            )),
        ]
        if milestones:
            remove = discord.ui.Select(
                placeholder="Remove a level role…",
                options=[discord.SelectOption(label=f"Level {m.level}", value=str(m.level)) for m in milestones[:25]],
            )
            remove.callback = self.remove_role
            items.append(self.row(remove))
        items.append(self.row(self.button("Add a level role", self.add_role, emoji="➕", style_=discord.ButtonStyle.success)))
        return items

    # --- callbacks ------------------------------------------------------------------

    def _goto(self, page: LevelPage):
        async def callback(interaction: discord.Interaction) -> None:
            self.page = page
            await self.rerender(interaction, changed=False)
        return callback

    def _flip(self, field: str):
        async def callback(interaction: discord.Interaction) -> None:
            config = await self.bot.guild_config.get(self.guild.id)
            await self.bot.guild_config.update(self.guild.id, **{field: not getattr(config, field)})
            await self.rerender(interaction)
        return callback

    async def pick_channel(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        await self.bot.guild_config.update(self.guild.id, levelup_channel_id=int(values[0]) if values else None)
        await self.rerender(interaction)

    async def edit_links(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        await interaction.response.send_modal(
            links_form(self.bot, self.guild, "levelup", link_buttons(config, "levelup"), self.rerender)
        )

    async def edit_message(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        if config.levelup_use_embed:
            await open_message_editor(interaction, "levelup")
            return

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            if not values[0]:
                raise ValueError("The message can't be empty.")
            await self.bot.guild_config.update(self.guild.id, levelup_text=values[0])
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Level-up text", [
            Field("Message", config.levelup_text, paragraph=True, max_length=500, required=True,
                  description="Plain text. {user} and {level} work here."),
        ], save))

    async def preview(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)
        message = self.bot.levelups.message(interaction.user, 5, config)
        content = "\n".join(part for part in ("-# Preview: you reaching level 5", message.get("content")) if part)
        await interaction.response.send_message(**{**message, "content": content}, ephemeral=True)

    async def edit_message_xp(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            low, high, cooldown = (_number(v, name, 0 if name == "Cooldown" else 1, 3600 if name == "Cooldown" else 100)
                                   for v, name in zip(values, ("Minimum XP", "Maximum XP", "Cooldown"), strict=True))
            if low > high:
                raise ValueError("The minimum XP can't be more than the maximum.")
            await self.bot.guild_config.update(self.guild.id, message_xp_min=low, message_xp_max=high, message_xp_cooldown=cooldown)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Message XP", [
            Field("Minimum XP per message", str(config.message_xp_min), max_length=3, required=True, description="1–100"),
            Field("Maximum XP per message", str(config.message_xp_max), max_length=3, required=True, description="1–100"),
            Field("Cooldown (seconds)", str(config.message_xp_cooldown), max_length=4, required=True,
                  description="0–3600. Messages sooner than this earn nothing."),
        ], save))

    async def edit_voice_xp(self, interaction: discord.Interaction) -> None:
        config = await self.bot.guild_config.get(self.guild.id)

        async def save(modal_interaction: discord.Interaction, values: list[str]) -> None:
            amount = _number(values[0], "XP per interval", 1, 100)
            interval = _number(values[1], "Interval", 60, 3600)
            minimum = _number(values[2], "Time before earning", 0, 3600)
            await self.bot.guild_config.update(self.guild.id, voice_xp_amount=amount, voice_xp_interval=interval,
                                               voice_min_session=minimum)
            await self.rerender(modal_interaction)

        await interaction.response.send_modal(Form("Voice XP", [
            Field("XP per interval", str(config.voice_xp_amount), max_length=3, required=True, description="1–100"),
            Field("Interval (seconds)", str(config.voice_xp_interval), max_length=4, required=True, description="60–3600"),
            Field("Time in voice before earning (s)", str(config.voice_min_session), max_length=4, required=True,
                  description="0–3600"),
        ], save))

    async def pick_mode(self, interaction: discord.Interaction) -> None:
        await self.bot.guild_config.update(self.guild.id, level_role_mode=interaction.data["values"][0])
        await self.rerender(interaction)

    async def remove_role(self, interaction: discord.Interaction) -> None:
        await self.bot.roles.remove_milestone(self.guild.id, int(interaction.data["values"][0]))
        await self.rerender(interaction)

    async def add_role(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddLevelRoleForm(self))


class AddLevelRoleForm(discord.ui.Modal):
    def __init__(self, editor: LevelingEditor) -> None:
        super().__init__(title="Add a level role", timeout=600)
        self.editor = editor
        self.level = discord.ui.TextInput(max_length=4, placeholder="e.g. 10")
        self.role = discord.ui.RoleSelect(required=False, min_values=0, max_values=1)
        self.add_item(discord.ui.Label(text="Level", description="1–1000", component=self.level))
        self.add_item(discord.ui.Label(text="Role to give", component=self.role,
                                       description="Leave empty to create a “Level N” role the first time someone reaches it."))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        editor, guild = self.editor, self.editor.guild
        try:
            level = _number(str(self.level.value), "Level", 1, 1000)
            role = self.role.values[0] if self.role.values else None
            if role is not None:
                if problem := role_problem(guild, role):
                    raise ValueError(f"{problem}.")
                member = interaction.user
                if isinstance(member, discord.Member) and member.id != guild.owner_id and role >= member.top_role:
                    raise ValueError(f"{role.mention} is at or above your highest role, so you can't hand it out.")
                taken = next((m for m in await editor.bot.roles.milestones(guild.id) if m.role_id == role.id), None)
                if taken is not None and taken.level != level:
                    raise ValueError(f"{role.mention} is already the level {taken.level} role.")
            elif not guild.me.guild_permissions.manage_roles:
                raise ValueError("I need the Manage Roles permission to create level roles.")
        except ValueError as exc:
            await interaction.response.send_message(embed=style.fail(str(exc)), ephemeral=True)
            return
        await editor.bot.roles.set_milestone(guild.id, level, role.id if role else None)
        await editor.rerender(interaction)


def _number(text: str, name: str, low: int, high: int) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number.") from None
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}.")
    return value


# ---- registration --------------------------------------------------------------------

SECTIONS = (
    PanelSection(key="welcome", label="Welcome & Goodbye", emoji="👋", order=10,
                 status=welcome_status, open=WelcomeEditor.open),
    PanelSection(key="leveling", label="Leveling", emoji="📈", order=20,
                 status=leveling_status, open=LevelingEditor.open),
)
