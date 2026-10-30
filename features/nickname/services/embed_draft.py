"""An embed being built with /embed create, plus validation of what admins type."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

import discord

# Discord's limits: https://discord.com/developers/docs/resources/message#embed-object-embed-limits
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096
FIELD_COUNT_LIMIT = 25
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
FOOTER_LIMIT = 2048
AUTHOR_LIMIT = 256
TOTAL_LIMIT = 6000
CONTENT_LIMIT = 2000
MENTION_LIMIT = 10

COLOUR_NAMES = {
    "red": 0xED4245,
    "green": 0x57F287,
    "blue": 0x3498DB,
    "blurple": 0x5865F2,
    "gold": 0xF1C40F,
    "yellow": 0xFEE75C,
    "orange": 0xE67E22,
    "purple": 0x9B59B6,
    "pink": 0xEB459E,
    "grey": 0x95A5A6,
    "gray": 0x95A5A6,
    "white": 0xFFFFFF,
    "black": 0x010101,  # 0 means "no colour" to Discord
}

_HEX_RE = re.compile(r"[0-9a-f]{6}")
_URL_RE = re.compile(r"https?://\S+")
YES = {"y", "yes", "true", "on", "1"}
NO = {"n", "no", "false", "off", "0"}


class DraftError(ValueError):
    pass


class RoleLike(Protocol):
    name: str
    mentionable: bool


def parse_colour(text: str) -> int | None:
    text = text.strip().lower()
    if not text:
        return None
    if text in COLOUR_NAMES:
        return COLOUR_NAMES[text]
    hex_part = text.removeprefix("#").removeprefix("0x")
    if _HEX_RE.fullmatch(hex_part):
        return int(hex_part, 16) or COLOUR_NAMES["black"]
    raise DraftError(f"“{text}” isn't a colour. Use a hex code like #5865F2 or a name like blurple.")


def parse_yes_no(text: str, default: bool = False) -> bool:
    text = text.strip().lower()
    if not text:
        return default
    if text in YES:
        return True
    if text in NO:
        return False
    raise DraftError(f"Answer “yes” or “no”, not “{text}”.")


def validate_url(text: str, label: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    if not _URL_RE.fullmatch(text):
        raise DraftError(f"{label} must be a link starting with http:// or https://.")
    return text


@dataclass
class EmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass
class EmbedDraft:
    title: str | None = None
    url: str | None = None
    description: str | None = None
    colour: int | None = None
    author_name: str | None = None
    author_icon: str | None = None
    author_url: str | None = None
    footer_text: str | None = None
    footer_icon: str | None = None
    timestamp: bool = False
    image_url: str | None = None
    thumbnail_url: str | None = None
    fields: list[EmbedField] = field(default_factory=list)
    # The plain message sent above the embed, and who it pings.
    message: str | None = None
    mention_role_ids: list[int] = field(default_factory=list)
    mention_user_ids: list[int] = field(default_factory=list)
    ping_everyone: bool = False
    ping_here: bool = False

    def is_empty(self) -> bool:
        return not any((
            self.title, self.description, self.fields, self.author_name,
            self.footer_text, self.image_url, self.thumbnail_url,
        ))

    def total_length(self) -> int:
        parts = [self.title, self.description, self.author_name, self.footer_text]
        parts += [f.name for f in self.fields] + [f.value for f in self.fields]
        return sum(len(p) for p in parts if p)

    def add_field(self, name: str, value: str, inline: bool) -> None:
        if len(self.fields) >= FIELD_COUNT_LIMIT:
            raise DraftError(f"An embed can have at most {FIELD_COUNT_LIMIT} fields.")
        if not name.strip() or not value.strip():
            raise DraftError("A field needs both a name and a value.")
        self.fields.append(EmbedField(name.strip(), value.strip(), inline))

    def remove_field(self, number: str) -> None:
        try:
            index = int(number.strip()) - 1
        except ValueError:
            raise DraftError("Enter the field's number, as shown in the preview.") from None
        if not 0 <= index < len(self.fields):
            raise DraftError(f"There's no field {number.strip()}. Pick a number from 1 to {len(self.fields)}.")
        del self.fields[index]

    def mention_tokens(self) -> list[str]:
        tokens = ["@everyone"] if self.ping_everyone else []
        tokens += ["@here"] if self.ping_here else []
        tokens += [f"<@&{r}>" for r in self.mention_role_ids]
        tokens += [f"<@{u}>" for u in self.mention_user_ids]
        return tokens

    def message_content(self) -> str | None:
        parts = []
        if tokens := self.mention_tokens():
            parts.append(" ".join(tokens))
        if self.message:
            parts.append(self.message)
        return "\n".join(parts) or None

    def check(self) -> None:
        """Raise DraftError if Discord would reject this draft."""
        if self.url and not self.title:
            raise DraftError("Add a title: the title link only works with a title.")
        if (self.author_icon or self.author_url) and not self.author_name:
            raise DraftError("Add an author name: the author icon and link only show with a name.")
        if self.footer_icon and not self.footer_text:
            raise DraftError("Add footer text: the footer icon only shows with text.")
        if self.total_length() > TOTAL_LIMIT:
            raise DraftError(
                f"The embed's text adds up to {self.total_length()} characters; Discord allows {TOTAL_LIMIT}."
            )
        content = self.message_content()
        if content and len(content) > CONTENT_LIMIT:
            raise DraftError(f"The message above the embed is too long ({len(content)}/{CONTENT_LIMIT} characters).")

    def to_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            url=self.url if self.title else None,
            description=self.description,
            colour=self.colour,
            timestamp=discord.utils.utcnow() if self.timestamp else None,
        )
        if self.author_name:
            embed.set_author(name=self.author_name, url=self.author_url, icon_url=self.author_icon)
        if self.footer_text:
            embed.set_footer(text=self.footer_text, icon_url=self.footer_icon)
        if self.image_url:
            embed.set_image(url=self.image_url)
        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)
        for f in self.fields:
            embed.add_field(name=f.name, value=f.value, inline=f.inline)
        return embed


def allowed_mentions(
    draft: EmbedDraft,
    roles: Mapping[int, RoleLike | None],
    sender_can_mention_everyone: bool,
    bot_can_mention_everyone: bool,
) -> discord.AllowedMentions:
    """Work out exactly who may be pinged, or raise DraftError explaining why not.

    @everyone, @here, and roles that aren't mentionable need the Mention
    Everyone permission, for both the person sending and the bot.
    """
    restricted = []
    if draft.ping_everyone:
        restricted.append("@everyone")
    if draft.ping_here:
        restricted.append("@here")
    for role_id in draft.mention_role_ids:
        role = roles.get(role_id)
        if role is None:
            raise DraftError("One of the selected roles no longer exists. Pick the pings again.")
        if not role.mentionable:
            restricted.append(f"@{role.name}")

    if restricted:
        targets = ", ".join(restricted)
        if not sender_can_mention_everyone:
            raise DraftError(f"You need the **Mention Everyone** permission in that channel to ping {targets}.")
        if not bot_can_mention_everyone:
            raise DraftError(f"I need the **Mention Everyone** permission in that channel to ping {targets}.")

    return discord.AllowedMentions(
        everyone=draft.ping_everyone or draft.ping_here,
        roles=[discord.Object(r) for r in draft.mention_role_ids],
        users=[discord.Object(u) for u in draft.mention_user_ids],
        replied_user=False,
    )
