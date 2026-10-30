"""The /welcome and /goodbye command groups. Both are the same GreetingGroup class, one per kind.

Not an extension: the welcome and goodbye cogs each create and register their own group.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from features.community.services.greeting import MAX_CONTENT, build_greeting, greeting_settings, render_card
from features.community.services.guild_config import GuildConfig
from features.community.services.media import (
    MAX_IMAGE_BYTES,
    UPLOAD_PREFIX,
    ImageInfo,
    InvalidMedia,
    Kind,
    download_image,
    inspect_image,
)
from features.community.utils.checks import channel_problem_reply, resolve_channel
from features.community.utils.placeholders import help_text, render
from features.community.utils.views import TemplateModal
from core.interactions import DEFER, defer, reply

if TYPE_CHECKING:
    from core.bot import Bot as CommunityBot

MAX_TEXT = 1000
TITLES = {"welcome": "Welcome", "goodbye": "Goodbye"}
COLOURS = {"welcome": discord.Colour.blurple(), "goodbye": discord.Colour.dark_grey()}
# Who the admin pretends to be in previews.
ROLE_IN_PREVIEW = {"welcome": "the new member", "goodbye": "the member leaving"}
DM_LIMITATION = (
    "Discord only lets bots DM people they still share a server with, "
    "so this DM only reaches members who are also in another server with me."
)


def background_value(kind: Kind, source: str | None, stored: bool) -> str:
    if not source:
        return "Default gradient"
    value = f"Uploaded file `{source[len(UPLOAD_PREFIX):]}`" if source.startswith(UPLOAD_PREFIX) else source
    if not stored:
        value += f"\n⚠️ The stored copy is missing. Set it again with `/{kind} gif`."
    return value[:1024]


def settings_embed(guild: discord.Guild, config: GuildConfig, kind: Kind, background_stored: bool) -> discord.Embed:
    settings = greeting_settings(config, kind)
    channel, problem = resolve_channel(guild, settings.channel_id)
    channel_value = channel.mention if channel else "Not set"
    if problem and settings.channel_id is not None:
        channel_value += f"\n⚠️ {problem}"

    embed = discord.Embed(title=f"{TITLES[kind]} settings", colour=COLOURS[kind])
    embed.add_field(name="Status", value="✅ Enabled" if settings.enabled else "⏸️ Disabled")
    embed.add_field(name="Channel", value=channel_value)
    embed.add_field(name="Card", value="On" if settings.card_enabled else "Off")
    embed.add_field(name="Background", value=background_value(kind, settings.background_source, background_stored), inline=False)
    embed.add_field(name="Text", value=f"```\n{settings.text[:1000]}\n```", inline=False)
    if kind == "goodbye":
        dm_status = "✅ On" if config.goodbye_dm_enabled else "⏸️ Off"
        embed.add_field(name="Goodbye DM", value=f"{dm_status}\n-# {DM_LIMITATION}", inline=False)
        embed.add_field(name="DM text", value=f"```\n{config.goodbye_dm_text[:1000]}\n```", inline=False)
    embed.add_field(name="Placeholders", value=help_text(), inline=False)
    footer = f"Edit with /{kind} text · Try it with /{kind} preview"
    if kind == "goodbye":
        footer += " · Kicks and bans count as leaving too"
    embed.set_footer(text=footer)
    return embed


def text_modal(*, title: str, current: str, on_save) -> TemplateModal:
    return TemplateModal(
        title=title,
        label="Text ({user}, {server}, {membercount}, …)",
        current=current,
        max_length=MAX_TEXT,
        on_save=on_save,
    )


class GreetingGroup(app_commands.Group):
    """Command descriptions use {kind}, filled in per instance."""

    def __init__(self, bot: CommunityBot, kind: Kind) -> None:
        self.bot = bot
        self.kind: Kind = kind
        super().__init__(
            name=kind,
            description=f"Configure {kind} messages",
            guild_only=True,
            default_permissions=discord.Permissions(manage_guild=True),
        )
        if kind == "goodbye":
            GoodbyeDmGroup(bot, parent=self)
        for command in self.walk_commands():
            command.description = command.description.format(kind=kind)

    @property
    def title(self) -> str:
        return TITLES[self.kind]

    def _field(self, suffix: str) -> str:
        return f"{self.kind}_{suffix}"

    async def _store_background(
        self,
        interaction: discord.Interaction,
        *,
        url: str | None = None,
        file: discord.Attachment | None = None,
    ) -> tuple[str, ImageInfo, bytes] | None:
        """Download/read, check and store the image.

        Returns (source, info, data), or None after replying with the error.
        """
        assert interaction.guild is not None and self.bot.http_session is not None
        try:
            if file is not None:
                if file.size > MAX_IMAGE_BYTES:
                    raise InvalidMedia(f"That file is too large ({file.size / 1024 / 1024:.1f} MB, max 8 MB).")
                data = await file.read()
                source = f"{UPLOAD_PREFIX}{file.filename}"
            else:
                assert url is not None
                data = await download_image(self.bot.http_session, url)
                source = url.strip()
            info = await asyncio.to_thread(inspect_image, data)
        except InvalidMedia as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return None
        except discord.HTTPException:
            await interaction.followup.send("❌ I couldn't read the uploaded file. Try again.", ephemeral=True)
            return None

        await self.bot.media.save(interaction.guild.id, self.kind, data)
        return source, info, data

    @app_commands.command(name="setup", description="Set the {kind} channel, and optionally the text and GIF.")
    @app_commands.describe(
        channel="Where the messages are posted",
        text="Message text (use the text command for multiple lines)",
        gif="Direct https link to a GIF or image for the card background",
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        text: app_commands.Range[str, 1, MAX_TEXT] | None = None,
        gif: str | None = None,
    ) -> None:
        assert interaction.guild is not None
        if problem := channel_problem_reply(channel):
            await reply(interaction, f"❌ {problem}", ephemeral=True)
            return

        await defer(interaction, ephemeral=True, thinking=True)
        fields: dict[str, object] = {self._field("channel_id"): channel.id}
        if text is not None:
            fields[self._field("text")] = text
        if gif is not None:
            stored = await self._store_background(interaction, url=gif)
            if stored is None:
                return
            fields[self._field("gif_url")] = stored[0]

        config = await self.bot.guild_config.update(interaction.guild.id, **fields)
        enabled = greeting_settings(config, self.kind).enabled
        status = "enabled" if enabled else f"**disabled** – run `/{self.kind} enable` when you're happy"
        await interaction.followup.send(
            f"✅ {self.title} messages will be posted in {channel.mention}. They are {status}.\n"
            f"Run `/{self.kind} preview` to see exactly what they'll look like.",
            ephemeral=True,
        )

    @app_commands.command(name="channel", description="Set the channel {kind} messages are posted in.")
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        assert interaction.guild is not None
        if problem := channel_problem_reply(channel):
            await reply(interaction, f"❌ {problem}", ephemeral=True)
            return
        await self.bot.guild_config.update(interaction.guild.id, **{self._field("channel_id"): channel.id})
        await reply(interaction, f"✅ {self.title} channel set to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="text", description="Edit the {kind} text (supports multiple lines).", extras={DEFER: False})
    async def text(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)

        async def save(modal_interaction: discord.Interaction, value: str) -> None:
            assert modal_interaction.guild is not None and isinstance(modal_interaction.user, discord.Member)
            await self.bot.guild_config.update(modal_interaction.guild.id, **{self._field("text"): value})
            example = render(value, modal_interaction.user)
            await reply(modal_interaction, 
                f"✅ {self.title} text saved. With you as {ROLE_IN_PREVIEW[self.kind]} it reads:\n\n{example}"[
                    :MAX_CONTENT
                ],
                ephemeral=True,
            )

        current = greeting_settings(config, self.kind).text
        await interaction.response.send_modal(text_modal(title=f"{self.title} text", current=current, on_save=save))

    @app_commands.command(name="gif", description="Set the {kind} card's background GIF/image, or give nothing to remove it.")
    @app_commands.describe(
        url="Direct https link to a GIF or image",
        file="Or upload the GIF/image here (max 8 MB)",
    )
    async def gif(
        self,
        interaction: discord.Interaction,
        url: str | None = None,
        file: discord.Attachment | None = None,
    ) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        if url is not None and file is not None:
            await reply(interaction, "❌ Give either a link or a file, not both.", ephemeral=True)
            return
        if url is None and file is None:
            await self.bot.media.delete(interaction.guild.id, self.kind)
            await self.bot.guild_config.update(interaction.guild.id, **{self._field("gif_url"): None})
            await reply(interaction, 
                "✅ Background removed. The card uses the default gradient again.", ephemeral=True
            )
            return

        await defer(interaction, ephemeral=True, thinking=True)
        stored = await self._store_background(interaction, url=url, file=file)
        if stored is None:
            return
        source, info, data = stored
        config = await self.bot.guild_config.update(interaction.guild.id, **{self._field("gif_url"): source})

        card: discord.File | None = None
        if greeting_settings(config, self.kind).card_enabled:
            card = await render_card(interaction.user, self.kind, data)
            note = f"Here's the card with you as {ROLE_IN_PREVIEW[self.kind]}:"
        else:
            note = f"The card is off, so this image is posted on its own under the {self.kind} text."
        await interaction.followup.send(
            f"✅ Background saved ({info.describe()}). {note}",
            **({"file": card} if card else {}),
            ephemeral=True,
        )

    @app_commands.command(name="card", description="Turn the {kind} image card on or off.")
    async def card(self, interaction: discord.Interaction, enabled: bool) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, **{self._field("card_enabled"): enabled})
        await reply(interaction, 
            f"✅ {self.title} card turned {'on' if enabled else 'off'}.", ephemeral=True
        )

    @app_commands.command(name="enable", description="Start sending {kind} messages.")
    async def enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)
        channel, problem = resolve_channel(interaction.guild, greeting_settings(config, self.kind).channel_id)
        if channel is None or problem:
            await reply(interaction, 
                f"❌ Can't enable yet: {problem}. Set one with `/{self.kind} channel`.", ephemeral=True
            )
            return
        await self.bot.guild_config.update(interaction.guild.id, **{self._field("enabled"): True})
        await reply(interaction, 
            f"✅ {self.title} messages enabled in {channel.mention}.", ephemeral=True
        )

    @app_commands.command(name="disable", description="Stop sending {kind} messages.")
    async def disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, **{self._field("enabled"): False})
        await reply(interaction, f"✅ {self.title} messages disabled.", ephemeral=True)

    @app_commands.command(name="preview", description="See the {kind} message, using yourself as the member.")
    async def preview(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None and isinstance(interaction.user, discord.Member)
        await defer(interaction, ephemeral=True, thinking=True)
        config = await self.bot.guild_config.get(interaction.guild.id)
        settings = greeting_settings(config, self.kind)
        channel, problem = resolve_channel(interaction.guild, settings.channel_id)

        destination = channel.mention if channel else "no channel"
        status = "enabled" if settings.enabled else "disabled"
        header = f"-# Preview · would post in {destination} · {status}"
        if problem:
            header += f" · ⚠️ {problem}"

        message = await build_greeting(
            interaction.user, self.kind, config, self.bot.media, self.bot.http_session,
            brand=lambda embed: self.bot.theme.brand(embed, interaction.guild),
        )
        message.content = "\n".join(part for part in (header, message.content) if part)[:MAX_CONTENT]
        await interaction.followup.send(**message.send_kwargs(), ephemeral=True)

        if self.kind == "goodbye" and config.goodbye_dm_enabled:
            dm_text = render(config.goodbye_dm_text, interaction.user)
            await interaction.followup.send(
                f"-# Preview · DM sent to the member who left (if Discord allows it)\n{dm_text}"[:MAX_CONTENT],
                ephemeral=True,
            )

    @app_commands.command(name="settings", description="Show the current {kind} settings.")
    async def settings(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)
        stored = await self.bot.media.exists(interaction.guild.id, self.kind)
        await reply(interaction, 
            embed=settings_embed(interaction.guild, config, self.kind, stored), ephemeral=True
        )


class GoodbyeDmGroup(app_commands.Group):
    """/goodbye dm …: an optional DM to the member who left."""

    def __init__(self, bot: CommunityBot, parent: GreetingGroup) -> None:
        self.bot = bot
        super().__init__(name="dm", description="Optional DM to members who leave", parent=parent)

    @app_commands.command(name="enable", description="Also DM members when they leave (best effort).")
    async def enable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, goodbye_dm_enabled=True)
        await reply(interaction, f"✅ Goodbye DMs turned on.\n-# {DM_LIMITATION}", ephemeral=True)

    @app_commands.command(name="disable", description="Stop DMing members when they leave.")
    async def disable(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        await self.bot.guild_config.update(interaction.guild.id, goodbye_dm_enabled=False)
        await reply(interaction, "✅ Goodbye DMs turned off.", ephemeral=True)

    @app_commands.command(name="text", description="Edit the goodbye DM text (supports multiple lines).", extras={DEFER: False})
    async def text(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.guild_config.get(interaction.guild.id)

        async def save(modal_interaction: discord.Interaction, value: str) -> None:
            assert modal_interaction.guild is not None and isinstance(modal_interaction.user, discord.Member)
            await self.bot.guild_config.update(modal_interaction.guild.id, goodbye_dm_text=value)
            example = render(value, modal_interaction.user)
            await reply(modal_interaction, 
                f"✅ Goodbye DM text saved. With you as the member leaving it reads:\n\n{example}"[:MAX_CONTENT],
                ephemeral=True,
            )

        await interaction.response.send_modal(
            text_modal(title="Goodbye DM text", current=config.goodbye_dm_text, on_save=save)
        )
