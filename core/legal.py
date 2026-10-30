"""Privacy policy and terms pages.

Placeholder until every feature is merged; the full combined text is written in the final phase
so it describes exactly what the merged bot does.
"""

from __future__ import annotations

import html

from core import config

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 16px;line-height:1.6}}</style>
</head><body><h1>{title}</h1>{body}</body></html>"""


def _page(title: str, body: str) -> str:
    name = html.escape(config.BOT_NAME or "This bot")
    contact = html.escape(config.CONTACT_EMAIL) if config.CONTACT_EMAIL else "the bot operator"
    return PAGE.format(title=f"{name} — {title}", body=body.format(name=name, contact=contact))


def privacy_page() -> str:
    return _page("Privacy Policy", "<p>{name}'s privacy policy is being updated. Questions: {contact}.</p>")


def terms_page() -> str:
    return _page("Terms of Service", "<p>{name}'s terms of service are being updated. Questions: {contact}.</p>")
