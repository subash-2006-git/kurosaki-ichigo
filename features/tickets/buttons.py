"""Ticket buttons: Open (on the posted message), Claim and Close (inside each ticket).

All three are DynamicItems, so they keep working after restarts. Claim and Close find their ticket
from the channel they're clicked in.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import discord

from core.ui import style
from core.ui.embed_draft import EmbedDraft
from core.ui.theme import brand

log = logging.getLogger(__name__)

TRANSCRIPT_LIMIT = 2000
DELETE_DELAY = 5
DEFAULT_OPENING: dict[str, Any] = {
    "title": "🎫 Need help?",
    "description": "Press the button below to open a private ticket with the staff team.\n"
                   "Only you and the staff can see it.",
}


# ---- helpers (pure, tested) -----------------------------------------------------------


def channel_name(number: int) -> str:
    return f"ticket-{number:04d}"


def overwrites_for(default_role, opener, staff_roles, me) -> dict:
    """Hidden from everyone; the opener, the staff roles and the bot can talk."""
    member_access = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True,
    )
    overwrites = {default_role: discord.PermissionOverwrite(view_channel=False), opener: member_access}
    for role in staff_roles:
        overwrites[role] = member_access
    overwrites[me] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, read_message_history=True, manage_channels=True,
        embed_links=True, attach_files=True,
    )
    return overwrites


def transcript_text(ticket, guild_name: str, messages, closed_by: str) -> str:
    header = [
        f"Ticket #{ticket.number} — {guild_name}",
        f"Opened by user {ticket.opener_id} at {ticket.created_at:%Y-%m-%d %H:%M} UTC",
        f"Reason: {ticket.reason or '(none given)'}",
        f"Closed by {closed_by}",
        "-" * 60,
    ]
    lines = []
    for m in messages:
        stamp = m.created_at.strftime("%Y-%m-%d %H:%M")
        text = m.content or ""
        extras = [a.url for a in m.attachments] + [f"[embed: {e.title or e.description or ''}]" for e in m.embeds]
        body = " ".join(part for part in [text, *extras] if part) or "(no text)"
        lines.append(f"[{stamp}] {m.author} ({m.author.id}): {body}")
    return "\n".join(header + (lines or ["(no messages)"])) + "\n"


def is_staff(member: discord.Member, settings) -> bool:
    if member.guild_permissions.manage_channels:
        return True
    return any(role.id in settings.staff_role_ids for role in member.roles)


def opening_message(settings, guild: discord.Guild) -> dict:
    draft = EmbedDraft.from_dict(settings.opening_embed or DEFAULT_OPENING)
    view = discord.ui.View(timeout=None)
    view.add_item(OpenTicketButton(label=settings.button_label))
    return {"embed": brand(draft.to_embed(), guild), "view": view}


def ticket_controls(claimed_by: str | None = None) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(ClaimTicketButton(claimed_by=claimed_by))
    view.add_item(CloseTicketButton())
    return view


# ---- opening ---------------------------------------------------------------------------


class ReasonForm(discord.ui.Modal, title="Open a ticket"):
    reason = discord.ui.TextInput(label="What do you need help with?", style=discord.TextStyle.paragraph,
                                  max_length=500, required=False)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await open_ticket(interaction, str(self.reason.value or "").strip() or None)


class OpenTicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:open"):
    def __init__(self, *, label: str = "Open a ticket") -> None:
        super().__init__(discord.ui.Button(label=label[:80], emoji="🎫", style=discord.ButtonStyle.primary,
                                           custom_id="ticket:open"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> OpenTicketButton:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        settings = await interaction.client.tickets_db.settings(interaction.guild_id)
        if problem := await _cannot_open(interaction, settings):
            await interaction.response.send_message(embed=problem, ephemeral=True)
            return
        if settings.ask_reason:
            await interaction.response.send_modal(ReasonForm())
        else:
            await open_ticket(interaction, None)


async def _cannot_open(interaction: discord.Interaction, settings) -> discord.Embed | None:
    if not settings.enabled:
        return style.warn("Tickets are closed right now. Please try again later.")
    existing = await interaction.client.tickets_db.open_for(interaction.guild_id, interaction.user.id)
    live = [t for t in existing if interaction.guild.get_channel(t.channel_id)]
    if len(live) >= settings.max_open:
        where = ", ".join(f"<#{t.channel_id}>" for t in live)
        return style.warn(f"You already have an open ticket: {where}")
    if not interaction.guild.me.guild_permissions.manage_channels:
        return style.fail("I need the **Manage Channels** permission to open tickets. Please tell a server admin.")
    return None


async def open_ticket(interaction: discord.Interaction, reason: str | None) -> None:
    bot, guild, opener = interaction.client, interaction.guild, interaction.user
    await interaction.response.defer(ephemeral=True, thinking=True)
    settings = await bot.tickets_db.settings(guild.id)
    if problem := await _cannot_open(interaction, settings):  # checked again: the form may have sat open
        await interaction.followup.send(embed=problem, ephemeral=True)
        return
    category = guild.get_channel(settings.category_id) if settings.category_id else None
    staff = [r for r in (guild.get_role(i) for i in settings.staff_role_ids) if r is not None]
    number = await bot.tickets_db.next_number(guild.id)
    try:
        channel = await guild.create_text_channel(
            channel_name(number),
            category=category if isinstance(category, discord.CategoryChannel) else None,
            overwrites=overwrites_for(guild.default_role, opener, staff, guild.me),
            topic=f"Ticket #{number} opened by {opener} ({opener.id})",
            reason=f"Ticket #{number} opened by {opener} ({opener.id})",
        )
    except discord.HTTPException as exc:
        log.warning("Couldn't create ticket channel in guild %s: %s", guild.id, exc)
        await interaction.followup.send(embed=style.fail("I couldn't create your ticket channel. Please tell a server admin."),
                                        ephemeral=True)
        return
    await bot.tickets_db.create(guild.id, number, channel.id, opener.id, reason)

    embed = style.info(f"🎫 Ticket #{number}", f"{opener.mention} opened this ticket. The staff team will be with you soon.")
    embed.add_field(name="Reason", value=reason or "*Not given*", inline=False)
    embed.set_thumbnail(url=opener.display_avatar.url)
    embed.timestamp = discord.utils.utcnow()
    brand(embed, guild)
    pings = " ".join([opener.mention, *(r.mention for r in staff)])
    await channel.send(pings, embed=embed, view=ticket_controls(),
                       allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False))
    await interaction.followup.send(embed=style.ok(f"Your ticket is open: {channel.mention}"), ephemeral=True)
    await bot.panel.refresh(guild)


# ---- claim and close -------------------------------------------------------------------


class ClaimTicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:claim"):
    def __init__(self, *, claimed_by: str | None = None) -> None:
        super().__init__(discord.ui.Button(
            label=f"Claimed by {claimed_by}"[:80] if claimed_by else "Claim", emoji="🙋",
            style=discord.ButtonStyle.secondary, custom_id="ticket:claim", disabled=claimed_by is not None,
        ))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> ClaimTicketButton:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        ticket = await bot.tickets_db.by_channel(interaction.channel_id)
        settings = await bot.tickets_db.settings(interaction.guild_id)
        if ticket is None or ticket.status != "open":
            await interaction.response.send_message(embed=style.warn("This ticket is already closed."), ephemeral=True)
            return
        if not is_staff(interaction.user, settings):
            await interaction.response.send_message(embed=style.fail("Only staff can claim tickets."), ephemeral=True)
            return
        if not await bot.tickets_db.claim(ticket.id, interaction.user.id):
            await interaction.response.send_message(embed=style.warn("Someone already claimed this ticket."), ephemeral=True)
            return
        await interaction.response.edit_message(view=ticket_controls(claimed_by=interaction.user.display_name))
        await interaction.followup.send(embed=brand(style.info(None, f"🙋 {interaction.user.mention} is handling this ticket."),
                                                    interaction.guild))


class CloseTicketButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:close"):
    def __init__(self) -> None:
        super().__init__(discord.ui.Button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger,
                                           custom_id="ticket:close"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /) -> CloseTicketButton:
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        ticket = await bot.tickets_db.by_channel(interaction.channel_id)
        settings = await bot.tickets_db.settings(interaction.guild_id)
        if ticket is None or ticket.status != "open":
            await interaction.response.send_message(embed=style.warn("This ticket is already closed."), ephemeral=True)
            return
        if interaction.user.id != ticket.opener_id and not is_staff(interaction.user, settings):
            await interaction.response.send_message(embed=style.fail("Only staff or the member who opened it can close it."),
                                                    ephemeral=True)
            return
        confirm = discord.ui.View(timeout=60)
        yes = discord.ui.Button(label="Close ticket", emoji="🔒", style=discord.ButtonStyle.danger)

        async def do_close(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.edit_message(embed=style.info("Closing…"), view=None)
            await close_ticket(bot, interaction.channel, ticket, confirm_interaction.user)

        yes.callback = do_close
        confirm.add_item(yes)
        await interaction.response.send_message(
            embed=style.warn("Close this ticket? A transcript is saved, then the channel is deleted."),
            view=confirm, ephemeral=True,
        )


async def close_ticket(bot, channel: discord.TextChannel, ticket, closer: discord.abc.User) -> bool:
    """Save a transcript, notify, and delete the channel. False if someone else closed it first."""
    if not await bot.tickets_db.close(ticket.id, closer.id):
        return False
    guild = channel.guild
    settings = await bot.tickets_db.settings(guild.id)
    await channel.send(embed=brand(style.info(None, f"🔒 Closed by {closer.mention}. This channel will be deleted in "
                                                    f"{DELETE_DELAY} seconds."), guild))
    messages = [m async for m in channel.history(limit=TRANSCRIPT_LIMIT, oldest_first=True)]
    text = transcript_text(ticket, guild.name, messages, f"{closer} ({closer.id})")
    filename = f"{channel_name(ticket.number)}.txt"

    summary = style.info(f"🎫 Ticket #{ticket.number} closed")
    summary.add_field(name="Opened by", value=f"<@{ticket.opener_id}>")
    summary.add_field(name="Closed by", value=closer.mention)
    if ticket.claimed_by:
        summary.add_field(name="Handled by", value=f"<@{ticket.claimed_by}>")
    summary.add_field(name="Reason", value=ticket.reason or "*Not given*", inline=False)
    summary.add_field(name="Messages", value=str(len(messages)))
    summary.timestamp = discord.utils.utcnow()
    brand(summary, guild)

    log_channel = guild.get_channel(settings.log_channel_id) if settings.log_channel_id else None
    if isinstance(log_channel, discord.abc.Messageable):
        try:
            await log_channel.send(embed=summary, file=discord.File(io.BytesIO(text.encode()), filename=filename),
                                   allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            log.warning("Ticket transcript not posted in guild %s: %s", guild.id, exc)
    opener = guild.get_member(ticket.opener_id)
    if opener is not None and not opener.bot:  # bots can't be DMed
        try:
            await opener.send(embed=summary, file=discord.File(io.BytesIO(text.encode()), filename=filename))
        except discord.HTTPException:
            pass

    await asyncio.sleep(DELETE_DELAY)
    try:
        await channel.delete(reason=f"Ticket #{ticket.number} closed by {closer} ({closer.id})")
    except discord.HTTPException as exc:
        log.warning("Ticket channel %s not deleted: %s", channel.id, exc)
    await bot.panel.refresh(guild)
    return True
