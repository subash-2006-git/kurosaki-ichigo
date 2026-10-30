"""Lightweight stand-ins for discord.py objects (no gateway connection needed)."""

from __future__ import annotations

import io
from types import SimpleNamespace

from PIL import Image


def png_bytes(colour=(240, 160, 60)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(output, format="PNG")
    return output.getvalue()


class FakeAsset:
    def __init__(self, data: bytes | None) -> None:
        self._data = data

    def replace(self, **_kwargs) -> FakeAsset:
        return self

    async def read(self) -> bytes:
        if self._data is None:
            import discord

            raise discord.DiscordException("avatar unavailable")
        return self._data


def fake_member(
    *,
    name: str = "subash",
    display_name: str = "Subash",
    user_id: int = 111,
    guild_name: str = "Lost Island",
    member_count: int | None = 1284,
    avatar: bytes | None = None,
):
    guild = SimpleNamespace(id=999, name=guild_name, member_count=member_count, members=[])
    return SimpleNamespace(
        id=user_id,
        name=name,
        display_name=display_name,
        mention=f"<@{user_id}>",
        guild=guild,
        display_avatar=FakeAsset(avatar if avatar is not None else png_bytes()),
    )
