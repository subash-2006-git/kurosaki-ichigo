"""/suggest, the posted suggestion with its vote buttons, and staff accept/deny.

Every button is a DynamicItem carrying the suggestion's id, so they keep working after restarts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.ui import style
from core.ui.theme import brand

if TYPE_CHECKING:
    from core.bot import Bot

log = logging.getLogger(__name__)

MAX_LENGTH = 1000
# decision -> (status, emoji, button label, past tense, colour)
DECISIONS = {
    "accept": ("accepted", "✅", "Accept", "Accepted", style.SUCCESS),
    "deny": ("denied", "❌", "Deny", "Denied", style.ERROR),
}


# ---- the posted message (pure, tested) ---------------------------------------------------


def suggestion_embed(suggestion, up: int, down: int, author: discord.abc.User | None,
                     guild: discord.Guild | None) -> discord.Embed:
    embed = discord.Embed(title=f"💡 Suggestion #{suggestion.number}", description=suggestion.content)
    if author is not None:
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    else:
        embed.set_author(name=f"User {suggestion.author_id}")
    embed.add_field(name="Votes", value=f"👍 **{up}** · 👎 **{down}**")
    embed.timestamp = suggestion.created_at
    if suggestion.status == "open":
        embed.set_footer(text="Vote with the buttons below. Press your vote again to take it back.")
        return brand(embed, guild)
    _, emoji, _, label, colour = next(v for v in DECISIONS.values() if v[0] == suggestion.status)
    decision = f"{emoji} **{label}** by <@{suggestion.decided_by}>"
    if suggestion.decision_reason:
        decision += f"\n{suggestion.decision_reason}"
    embed.add_field(name="Decision", value=decision, inline=False)
    embed.colour = colour
    embed.set_footer(text=f"Voting closed · {label.lower()}")
    return embed


def suggestion_view(suggestion_id: int, up: int, down: int, is_open: bool = True) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(VoteButton(suggestion_id, 1, up, disabled=not is_open))
    view.add_item(VoteButton(suggestion_id, -1, down, disabled=not is_open))
    if is_open:
        view.add_item(DecideButton(suggestion_id, "accept"))
        view.add_item(DecideButton(suggestion_id, "deny"))
    return view


async def redraw(bot: Bot, suggestion, message: discord.Message | discord.PartialMessage | None = None) -> None:
    """Bring the posted message up to date with the votes and status. Never raises."""
    guild = bot.get_guild(suggestion.guild_id)
    if guild is None or not suggestion.message_id:
        return
    channel = guild.get_channel(suggestion.channel_id)
    if message is None:
        if not isinstance(channel, discord.abc.Messageable):
            return
        message = channel.get_partial_message(suggestion.message_id)
    up, down = await bot.suggestions_db.tally(suggestion.id)
    author = guild.get_member(suggestion.author_id) or bot.get_user(suggestion.author_id)
    try:
        await message.edit(embed=suggestion_embed(suggestion, up, down, author, guild),
                           view=suggestion_view(suggestion.id, up, down, suggestion.status == "open"))
    except discord.HTTPException as exc:
        log.warning("Suggestion %s not redrawn: %s", suggestion.id, exc)


# ---- buttons -----------------------------------------------------------------------------


class VoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"suggest:vote:(?P<id>\d+):(?P<vote>up|down)"):
    def __init__(self, suggestion_id: int, vote: int, count: int = 0, *, disabled: bool = False) -> None:
        self.suggestion_id, self.vote = suggestion_id, vote
        super().__init__(discord.ui.Button(
            label=str(count), emoji="👍" if vote == 1 else "👎", disabled=disabled,
            style=discord.ButtonStyle.success if vote == 1 else discord.ButtonStyle.danger,
            custom_id=f"suggest:vote:{suggestion_id}:{'up' if vote == 1 else 'down'}",
        ))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> VoteButton:
        return cls(int(match["id"]), 1 if match["vote"] == "up" else -1)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        suggestion = await bot.suggestions_db.get(self.suggestion_id)
        if suggestion is None or suggestion.status != "open":
            await interaction.response.send_message(embed=style.warn("Voting on this suggestion has closed."), ephemeral=True)
            return
        up, down, now = await bot.suggestions_db.vote(suggestion.id, interaction.user.id, self.vote)
        author = interaction.guild.get_member(suggestion.author_id) or bot.get_user(suggestion.author_id)
        await interaction.response.edit_message(embed=suggestion_embed(suggestion, up, down, author, interaction.guild),
                                                view=suggestion_view(suggestion.id, up, down))
        text = {1: "You voted 👍.", -1: "You voted 👎.", 0: "Your vote was taken back."}[now]
        await interaction.followup.send(text, ephemeral=True)


class DecisionForm(discord.ui.Modal):
    reason = discord.ui.TextInput(label="Reason (optional)", style=discord.TextStyle.paragraph, max_length=500,
                                  required=False)

    def __init__(self, suggestion_id: int, decision: str) -> None:
        super().__init__(title=f"{DECISIONS[decision][2]} this suggestion")
        self.suggestion_id, self.decision = suggestion_id, decision

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        status, emoji, _, label, _ = DECISIONS[self.decision]
        reason = str(self.reason.value or "").strip() or None
        suggestion = await bot.suggestions_db.decide(self.suggestion_id, status, interaction.user.id, reason)
        if suggestion is None:
            await interaction.response.send_message(embed=style.warn("Someone already decided on this suggestion."),
                                                    ephemeral=True)
            return
        await interaction.response.defer()
        await redraw(bot, suggestion, interaction.message)
        await _tell_author(bot, interaction.guild, suggestion, emoji, label)


async def _tell_author(bot: Bot, guild: discord.Guild, suggestion, emoji: str, label: str) -> None:
    author = guild.get_member(suggestion.author_id)
    if author is None or author.bot:
        return
    link = f"https://discord.com/channels/{guild.id}/{suggestion.channel_id}/{suggestion.message_id}"
    embed = style.info(f"{emoji} Your suggestion was {label.lower()}",
                       f"**#{suggestion.number}** in **{guild.name}**: {suggestion.content[:300]}\n\n"
                       + (f"**Reason:** {suggestion.decision_reason}\n" if suggestion.decision_reason else "")
                       + f"[Jump to it]({link})")
    try:
        await author.send(embed=brand(embed, guild))
    except discord.HTTPException:
        pass


class DecideButton(discord.ui.DynamicItem[discord.ui.Button], template=r"suggest:(?P<decision>accept|deny):(?P<id>\d+)"):
    def __init__(self, suggestion_id: int, decision: str) -> None:
        self.suggestion_id, self.decision = suggestion_id, decision
        _, emoji, label, _, _ = DECISIONS[decision]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                           custom_id=f"suggest:{decision}:{suggestion_id}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> DecideButton:
        return cls(int(match["id"]), match["decision"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message(
                embed=style.fail("Only staff (**Manage Messages**) can accept or deny suggestions."), ephemeral=True)
            return
        await interaction.response.send_modal(DecisionForm(self.suggestion_id, self.decision))


# ---- /suggest ----------------------------------------------------------------------------


class Suggestions(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    @app_commands.command(name="suggest", description="Post a suggestion for the server to vote on")
    @app_commands.describe(idea="Your suggestion")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 60, key=lambda i: (i.guild_id, i.user.id))
    async def suggest(self, interaction: discord.Interaction,
                      idea: app_commands.Range[str, 1, MAX_LENGTH]) -> None:
        guild = interaction.guild
        settings = await self.bot.suggestions_db.settings(guild.id)
        channel = guild.get_channel(settings.channel_id) if settings.channel_id else None
        if not settings.enabled or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(embed=style.warn("Suggestions aren't turned on in this server."),
                                                    ephemeral=True)
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                embed=style.fail(f"I can't post in {channel.mention}. Please tell a server admin."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        suggestion = await self.bot.suggestions_db.create(guild.id, interaction.user.id, channel.id, idea.strip())
        try:
            message = await channel.send(embed=suggestion_embed(suggestion, 0, 0, interaction.user, guild),
                                         view=suggestion_view(suggestion.id, 0, 0))
        except discord.HTTPException as exc:
            await self.bot.suggestions_db.delete(suggestion.id)
            log.warning("Suggestion not posted in guild %s: %s", guild.id, exc)
            await interaction.followup.send(embed=style.fail("I couldn't post your suggestion. Please tell a server admin."),
                                            ephemeral=True)
            return
        await self.bot.suggestions_db.set_message(suggestion.id, message.id)
        if settings.threads and perms.create_public_threads:
            try:
                await message.create_thread(name=f"Suggestion #{suggestion.number}"[:100], auto_archive_duration=10080)
            except discord.HTTPException:
                pass
        await interaction.followup.send(embed=style.ok(f"Your suggestion is up: {message.jump_url}"), ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id:
            await self.bot.suggestions_db.forget_message(payload.message_id)
