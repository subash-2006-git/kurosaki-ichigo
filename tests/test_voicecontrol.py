"""Voice commands: who they act on, which channel, permissions, and the prefix-command error replies."""

from types import SimpleNamespace

import discord
import pytest
from discord.ext import commands

from core.errors import on_command_error, text_command_message
from features.voicecontrol.cog import ACTIONS, VoiceControl, plan, skipped_text, voice_channel_for
from features.voicecontrol.db import VoiceControlDB
from features.voicecontrol.panel import VoiceControlEditor

ME_ROLE = SimpleNamespace(id=500, position=10)


def member(id_, position=1, roles=(), mute=False, deaf=False):
    calls = []

    async def move_to(channel, reason=None):
        calls.append(("move", channel))

    async def edit(reason=None, **fields):
        calls.append(("edit", fields))

    return SimpleNamespace(id=id_, top_role=SimpleNamespace(id=1000 + id_, position=position),
                           roles=[SimpleNamespace(id=r) for r in roles], voice=SimpleNamespace(mute=mute, deaf=deaf),
                           move_to=move_to, edit=edit, calls=calls)


ME = SimpleNamespace(id=1, top_role=ME_ROLE)
SETTINGS = SimpleNamespace(exempt_role_ids=[70], exempt_user_ids=[6])


def test_plan_skips_the_right_people():
    members = [ME, member(2), member(3, position=11), member(4, position=20), member(5, roles=[70]), member(6),
               member(7), member(8, mute=True)]
    targets, skipped = plan(members, me=ME, invoker_id=7, owner_id=9, settings=SETTINGS, action=ACTIONS["server-mute"])
    assert [m.id for m in targets] == [2]
    assert skipped == {"you": 1, "exempt": 2, "above": 2, "already": 1}
    assert skipped_text(skipped, ACTIONS["server-mute"]) == (
        "you, 2 skipped in the panel, 2 at or above my role, 1 already muted")
    owner = member(9)
    assert plan([owner], me=ME, invoker_id=7, owner_id=9, settings=SETTINGS, action=ACTIONS["disconnect"])[1]["above"] == 1


def test_undo_actions_only_pick_those_affected():
    members = [member(2, mute=True, deaf=False), member(3, mute=False, deaf=True)]
    unmute, _ = plan(members, me=ME, invoker_id=0, owner_id=0, settings=SETTINGS, action=ACTIONS["server-unmute"])
    undeafen, _ = plan(members, me=ME, invoker_id=0, owner_id=0, settings=SETTINGS, action=ACTIONS["server-undeafen"])
    assert [m.id for m in unmute] == [2] and [m.id for m in undeafen] == [3]


def test_voice_channel_for():
    voice = discord.VoiceChannel.__new__(discord.VoiceChannel)
    assert voice_channel_for(SimpleNamespace(channel=voice, author=None)) is voice
    other = object()
    in_voice = SimpleNamespace(voice=SimpleNamespace(channel=other))
    assert voice_channel_for(SimpleNamespace(channel=object(), author=in_voice)) is other
    assert voice_channel_for(SimpleNamespace(channel=object(), author=SimpleNamespace(voice=None))) is None


class Ctx(SimpleNamespace):
    async def reply(self, embed=None, **kwargs):
        self.replies.append(embed.description)


def setup_run(author_perms=True, bot_perms=True):
    targets = [member(2), member(3)]
    channel = discord.VoiceChannel.__new__(discord.VoiceChannel)
    perms = {"me": SimpleNamespace(move_members=bot_perms), "author": SimpleNamespace(move_members=author_perms)}
    author = SimpleNamespace(id=7, voice=None, mention="<@7>")
    guild = SimpleNamespace(id=1, me=ME, owner_id=9)
    ctx = Ctx(prefix="!", channel=channel, author=author, guild=guild, replies=[])
    cog = VoiceControl.__new__(VoiceControl)
    logged = []

    async def settings(guild_id):
        return SimpleNamespace(exempt_role_ids=[], exempt_user_ids=[])

    cog.bot = SimpleNamespace(voicecontrol_db=SimpleNamespace(settings=settings))
    return cog, ctx, channel, perms, targets, logged


async def test_disconnect_all(monkeypatch):
    cog, ctx, channel, perms, targets, logged = setup_run()
    monkeypatch.setattr(discord.VoiceChannel, "permissions_for", lambda self, who: perms["me" if who is ME else "author"])
    monkeypatch.setattr(discord.VoiceChannel, "members", property(lambda self: targets))
    monkeypatch.setattr(discord.VoiceChannel, "mention", property(lambda self: "#voice"))
    monkeypatch.setattr(discord.VoiceChannel, "name", "voice", raising=False)

    async def send_log(bot, guild, embed):
        logged.append(embed.description)

    monkeypatch.setattr("features.voicecontrol.cog.send_log", send_log)
    await cog.run(ctx, "disconnect", "all")
    assert all(m.calls == [("move", None)] for m in targets)
    assert ctx.replies == ["✅ Disconnected **2** member(s) in #voice."]
    assert logged and "Disconnected 2" in logged[0]


async def test_permissions_and_scope_are_checked(monkeypatch):
    cog, ctx, channel, perms, targets, _ = setup_run(author_perms=False)
    monkeypatch.setattr(discord.VoiceChannel, "permissions_for", lambda self, who: perms["me" if who is ME else "author"])
    with pytest.raises(commands.MissingPermissions):
        await cog.run(ctx, "disconnect", None)
    perms["author"].move_members, perms["me"].move_members = True, False
    with pytest.raises(commands.BotMissingPermissions):
        await cog.run(ctx, "disconnect", None)
    async def no_role(self, ctx, argument):
        raise commands.RoleNotFound(argument)

    monkeypatch.setattr(commands.RoleConverter, "convert", no_role)
    with pytest.raises(commands.BadArgument, match="!disconnect all` or `!disconnect @role"):
        await cog.run(ctx, "disconnect", "nobody")
    assert all(m.calls == [] for m in targets)


def test_plan_with_a_role_only_counts_its_members():
    members = [member(2, roles=[80]), member(3), member(4, roles=[80], position=20), member(7, roles=[80])]
    targets, skipped = plan(members, me=ME, invoker_id=7, owner_id=9, settings=SETTINGS, action=ACTIONS["disconnect"],
                            role_id=80)
    assert [m.id for m in targets] == [2]
    assert skipped == {"you": 1, "exempt": 0, "above": 1, "already": 0}  # member 3 isn't part of it at all


MENTIONS = {100: "#source", 200: "#target"}


def voice(id_):
    channel = discord.VoiceChannel.__new__(discord.VoiceChannel)
    channel.id = id_
    return channel


async def test_move_role_to_another_channel(monkeypatch):
    cog, ctx, _, perms, targets, logged = setup_run()
    source, destination = voice(100), voice(200)
    ctx.channel = source
    targets[0].roles.append(SimpleNamespace(id=80))
    perms["me"].connect = perms["author"].connect = True
    monkeypatch.setattr(discord.VoiceChannel, "permissions_for", lambda self, who: perms["me" if who is ME else "author"])
    monkeypatch.setattr(discord.VoiceChannel, "members", property(lambda self: targets))
    monkeypatch.setattr(discord.VoiceChannel, "mention", property(lambda self: MENTIONS[self.id]))
    monkeypatch.setattr(discord.VoiceChannel, "name", "source", raising=False)
    role = SimpleNamespace(id=80, mention="<@&80>")

    async def find_role(self, ctx, argument):
        return role

    async def log(bot, guild, embed):
        logged.append(embed.description)

    monkeypatch.setattr(commands.RoleConverter, "convert", find_role)
    monkeypatch.setattr("features.voicecontrol.cog.send_log", log)
    await cog.run(ctx, "move", "@Guests", destination=destination)
    assert targets[0].calls == [("move", destination)] and targets[1].calls == []
    assert ctx.replies == ["✅ Moved **1** member(s) with <@&80> from #source to #target."]

    with pytest.raises(commands.BadArgument, match="already in that channel"):
        await cog.run(ctx, "move", "all", destination=source)
    perms["author"].connect = False
    with pytest.raises(commands.BadArgument, match="can't join"):
        await cog.run(ctx, "move", "all", destination=destination)


def test_prefix_error_messages():
    assert "Move Members" in text_command_message(commands.MissingPermissions(["move_members"]))
    assert text_command_message(commands.BadArgument("Join the voice channel first.")) == "Join the voice channel first."
    assert text_command_message(commands.CommandInvokeError(RuntimeError("x"))) is None


async def test_unknown_prefix_commands_are_ignored():
    replies = []

    async def reply(*args, **kwargs):
        replies.append(kwargs)

    await on_command_error(None, SimpleNamespace(reply=reply), commands.CommandNotFound("nope"))
    assert replies == []


async def test_settings_storage(core_db):
    db = VoiceControlDB(core_db.pool)
    assert (await db.settings(1)).exempt_role_ids == []
    s = await db.update(1, exempt_role_ids=[5, 6], exempt_user_ids=[7])
    assert (s.exempt_role_ids, s.exempt_user_ids) == ([5, 6], [7])
    with pytest.raises(ValueError):
        await db.update(1, counter=2)


async def test_panel_builds(core_db):
    db = VoiceControlDB(core_db.pool)
    await db.update(1, exempt_role_ids=[5], exempt_user_ids=[7])
    bot = SimpleNamespace(voicecontrol_db=db, theme=SimpleNamespace(get=lambda guild: SimpleNamespace(colour=1)))
    guild = SimpleNamespace(id=1, me=SimpleNamespace(guild_permissions=discord.Permissions(move_members=True)))
    editor = VoiceControlEditor(bot, guild, 1)
    await editor.build()
    texts = [c.content for c in editor.walk_children() if isinstance(c, discord.ui.TextDisplay)]
    assert "Mute Members, Deafen Members" in texts[0]  # warns about what's missing
    assert len(list(editor.walk_children())) <= 40
