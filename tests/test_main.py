import asyncio

import discord
import pytest
from aiohttp import web

import main


async def _serve(handler):
    app = web.Application()
    app.router.add_get("/api/v10/users/@me", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/api/v10/users/@me"


async def test_waits_through_rate_limit_then_returns(monkeypatch):
    calls = {"n": 0}

    async def handler(_request):
        calls["n"] += 1
        return web.Response(status=429) if calls["n"] < 3 else web.json_response({"id": "1"})

    runner, url = await _serve(handler)
    monkeypatch.setattr(main, "LOGIN_CHECK_URL", url)
    monkeypatch.setattr(main, "BACKOFF_START", 0)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 0)
    try:
        await asyncio.wait_for(main.wait_until_discord_allows_login("t"), 5)
    finally:
        await runner.cleanup()
    assert calls["n"] == 3


async def test_bad_token_raises_login_failure(monkeypatch):
    async def handler(_request):
        return web.Response(status=401)

    runner, url = await _serve(handler)
    monkeypatch.setattr(main, "LOGIN_CHECK_URL", url)
    try:
        with pytest.raises(discord.LoginFailure):
            await main.wait_until_discord_allows_login("bad")
    finally:
        await runner.cleanup()
