"""/embed create: build an embed with a live preview, then send it to a channel."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.ui.embed_draft import DraftError, allowed_mentions
from core.ui.embed_editor import EmbedEditor

if TYPE_CHECKING:
    from core.bot import Bot as NicknameBot

SENDABLE_CHANNELS = [discord.ChannelType.text, discord.ChannelType.news]


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


class BuilderView(EmbedEditor):
    """The /embed create editor: pings allowed, plus a channel picker, and Save sends the embed."""

    save_label = "Send embed"
    allow_pings = True
    closed_text = "This embed builder closed after 10 minutes without use. Run `/embed create` to start again."

    def __init__(self, author_id: int, channel: discord.abc.GuildChannel):
        self.channel = channel
        super().__init__(author_id)

    def extra_items(self) -> list[discord.ui.Item]:
        self.channel_select = discord.ui.ChannelSelect(
            channel_types=SENDABLE_CHANNELS, placeholder="Channel to send to", row=2,
            default_values=[discord.SelectDefaultValue(id=self.channel.id, type=discord.SelectDefaultValueType.channel)]
            if self.channel.type in SENDABLE_CHANNELS else [],
        )
        self.channel_select.callback = self.pick_channel
        return [self.channel_select]

    def header(self) -> str:
        return f"**Embed builder:** only you can see this. It will be sent to {self.channel.mention}."

    def _sync(self) -> None:
        super()._sync()
        self.save_button.disabled = self.draft.is_empty()

    async def pick_channel(self, interaction: discord.Interaction):
        picked = self.channel_select.values[0]
        self.channel = interaction.guild.get_channel(picked.id) or self.channel
        await self.refresh(interaction)

    async def save(self, interaction: discord.Interaction):
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
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        embed = self.draft.to_embed()
        interaction.client.theme.brand(embed, guild)
        try:
            message = await channel.send(content=self.draft.message_content(), embed=embed, allowed_mentions=mentions)
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"⚠️ Discord didn't accept the embed: {exc.text or exc.status}. Your draft is still here.",
                ephemeral=True,
            )
            return

        self.stop()
        await interaction.response.edit_message(
            content=f"✅ Sent to {channel.mention}: {message.jump_url}", embed=None, view=None
        )


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
