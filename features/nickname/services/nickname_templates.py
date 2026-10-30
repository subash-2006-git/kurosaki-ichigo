"""Nickname templates.

A template is plain text with optional placeholders:
  {name}      the member's base name (their pre-bot nickname or display name)
  {username}  the member's account username

"★ {name}" turns "Subash" into "★ Subash". A template without {name} sets
the same fixed nickname for everyone.
"""

import re

MAX_NICK_LENGTH = 32
NAME = "{name}"
USERNAME = "{username}"
PLACEHOLDERS = {"name", "username"}

_PLACEHOLDER_RE = re.compile(r"\{(\w*)\}")


class TemplateError(ValueError):
    pass


def validate_template(template: str) -> str:
    template = template.strip()
    if not template:
        raise TemplateError("The template can't be empty.")

    unknown = {m for m in _PLACEHOLDER_RE.findall(template) if m not in PLACEHOLDERS}
    if unknown:
        names = ", ".join("{" + u + "}" for u in sorted(unknown))
        raise TemplateError(f"Unknown placeholder {names}. Use {{name}} or {{username}}.")

    fixed_length = len(_PLACEHOLDER_RE.sub("", template))
    needed = fixed_length + (1 if NAME in template else 0)
    if needed > MAX_NICK_LENGTH:
        raise TemplateError(
            f"The template's fixed text is too long: nicknames are limited to {MAX_NICK_LENGTH} characters."
        )
    return template


def strip_template(template: str, nickname: str) -> str:
    """Undo a previous application of a prefix/suffix template.

    Only works for templates with exactly one {name} and no {username}; other
    templates are returned unchanged. Repeated applications are all removed,
    so "★ ★ Subash" becomes "Subash".
    """
    if template.count(NAME) != 1 or USERNAME in template:
        return nickname
    prefix, suffix = template.split(NAME)
    if not prefix and not suffix:
        return nickname

    while (
        nickname.startswith(prefix)
        and nickname.endswith(suffix)
        and len(nickname) > len(prefix) + len(suffix)
    ):
        nickname = nickname[len(prefix):len(nickname) - len(suffix)]
    return nickname


def render(template: str, base_name: str, username: str) -> str:
    """Build a nickname, shortening the name part if the result would be too long."""
    text = template.replace(USERNAME, username)
    count = text.count(NAME)
    if count:
        room = (MAX_NICK_LENGTH - (len(text) - count * len(NAME))) // count
        if room < 1:
            raise TemplateError("There's no room left for the name.")
        text = text.replace(NAME, base_name[:room].strip())
    return validate_nickname(text)


def validate_nickname(nickname: str) -> str:
    nickname = nickname.strip()
    if not nickname:
        raise TemplateError("The nickname would be empty.")
    if len(nickname) > MAX_NICK_LENGTH:
        raise TemplateError(f"The nickname would be longer than {MAX_NICK_LENGTH} characters.")
    return nickname
