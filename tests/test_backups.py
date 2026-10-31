"""Restoring a server's settings: reading the workflow's encrypted dumps, and replacing just one server's rows."""

import gzip
import io
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from core.backups import RestoreTarget, TableData, decrypt, parse_dump, read_backup, restore
from core.panel.backups import preview_lines

DUMP = """--
-- PostgreSQL database dump
--
COPY starboard.settings (guild_id, enabled, channel_id, emoji, threshold, allow_self) FROM stdin;
1\tt\t50\t⭐\t5\tf
2\tt\t60\t🌟\t2\tt
\\.

COPY public.other (id) FROM stdin;
9
\\.
"""


def test_parse_dump_reads_wanted_tables_only():
    data = parse_dump(DUMP, {"starboard.settings", "quotes.settings"})
    assert list(data) == ["starboard.settings"]
    table = data["starboard.settings"]
    assert table.columns == ["guild_id", "enabled", "channel_id", "emoji", "threshold", "allow_self"]
    assert table.for_guild(2) == ["2\tt\t60\t🌟\t2\tt"]


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl command")
def test_decrypts_what_the_backup_workflow_writes():
    plain = gzip.compress(DUMP.encode())
    encrypted = subprocess.run(  # the exact command in .github/workflows/backup.yml
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "pass:secret words"],
        input=plain, capture_output=True, check=True).stdout
    assert decrypt(encrypted, "secret words") == plain
    assert read_backup(encrypted, "secret words", {"starboard.settings"})["starboard.settings"].for_guild(1)
    with pytest.raises(ValueError, match="BACKUP_PASSPHRASE"):
        decrypt(encrypted, "wrong")
    with pytest.raises(ValueError, match="isn't an encrypted backup"):
        decrypt(b"plain text", "x")


async def dump_of(conn, table: str) -> TableData:
    """The table as pg_dump's COPY text would have it."""
    columns = [r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_schema || '.' || table_name = $1 "
        "ORDER BY ordinal_position", table)]
    out = io.BytesIO()
    schema, name = table.split(".")
    await conn.copy_from_table(name, schema_name=schema, output=out, format="text")
    return TableData(columns, out.getvalue().decode().splitlines())


async def test_restore_replaces_only_this_servers_rows(core_db):
    pool = core_db.pool
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO starboard.settings (guild_id, enabled, threshold) VALUES (1, TRUE, 5), (2, TRUE, 2)")
        await conn.execute("INSERT INTO tickets.settings (guild_id, counter) VALUES (1, 10)")
        await conn.execute("INSERT INTO tickets.types (guild_id, label) VALUES (1, 'Support'), (1, 'Appeal')")
        backup = {t: await dump_of(conn, t) for t in ("starboard.settings", "tickets.settings", "tickets.types")}
        # after the backup: settings change, a type is added, tickets keep being numbered
        await conn.execute("UPDATE starboard.settings SET threshold = 99")
        await conn.execute("UPDATE tickets.settings SET counter = 25, max_open = 3")
        await conn.execute("INSERT INTO tickets.types (guild_id, label) VALUES (1, 'Later')")
        await conn.execute("INSERT INTO quotes.settings (guild_id, enabled) VALUES (1, FALSE)")

    resets = []
    targets = [
        RestoreTarget("starboard.settings", "Starboard", reset=resets.append),
        RestoreTarget("tickets.settings", "Tickets", keep_max=("counter",)),
        RestoreTarget("tickets.types", "Types"),
        RestoreTarget("quotes.settings", "Quotes"),  # not in this backup: left alone
    ]
    restored = await restore(pool, targets, backup, 1)
    assert restored == {"starboard.settings": 1, "tickets.settings": 1, "tickets.types": 2}
    assert resets == [1]
    async with pool.acquire() as conn:
        thresholds = dict(await conn.fetch("SELECT guild_id, threshold FROM starboard.settings"))
        assert thresholds == {1: 5, 2: 99}  # server 2 untouched
        assert tuple(await conn.fetchrow("SELECT counter, max_open FROM tickets.settings WHERE guild_id = 1")) == (25, 1)
        assert sorted(r["label"] for r in await conn.fetch("SELECT label FROM tickets.types")) == ["Appeal", "Support"]
        assert await conn.fetchval("SELECT enabled FROM quotes.settings WHERE guild_id = 1") is False


async def test_counters_survive_when_the_backup_has_no_row(core_db):
    async with core_db.pool.acquire() as conn:
        backup = {"tickets.settings": await dump_of(conn, "tickets.settings")}  # empty: tickets weren't set up yet
        await conn.execute("INSERT INTO tickets.settings (guild_id, enabled, counter) VALUES (1, TRUE, 40)")
    await restore(core_db.pool, [RestoreTarget("tickets.settings", "Tickets", keep_max=("counter",))], backup, 1)
    row = await core_db.pool.fetchrow("SELECT enabled, counter FROM tickets.settings WHERE guild_id = 1")
    assert (row["enabled"], row["counter"]) == (False, 40)  # settings back to default, numbering continues


def test_preview_lines():
    targets = [RestoreTarget("a.b", "Alpha"), RestoreTarget("c.d", "Gamma")]
    assert preview_lines(targets, {"a.b": 2, "c.d": None}, {"a.b": 1, "c.d": 4}) == [
        "• **Alpha**: 2 in the backup · 1 now", "• **Gamma**: not in this backup (left as is) · 4 now"]


@pytest.mark.parametrize(("configured", "author", "expected"), [
    (False, 2, "BACKUP_REPO"),            # not set up: explains what's needed
    (True, 3, "Only the server owner"),   # a listed member can't restore
    (True, 2, None),                      # the owner gets the button
])
async def test_editor(monkeypatch, configured, author, expected):
    import discord

    import core.config as config
    from core.panel.backups import BackupsEditor

    for name in ("BACKUP_REPO", "GITHUB_TOKEN", "BACKUP_PASSPHRASE"):
        monkeypatch.setattr(config, name, "x" if configured else "")

    async def is_owner(user):
        return False

    bot = SimpleNamespace(is_owner=is_owner, theme=SimpleNamespace(get=lambda g: SimpleNamespace(colour=1)))
    editor = BackupsEditor(bot, SimpleNamespace(id=1, owner_id=2), author)
    await editor.build()
    texts = " ".join(c.content for c in editor.walk_children() if isinstance(c, discord.ui.TextDisplay))
    buttons = [c.label for c in editor.walk_children() if isinstance(c, discord.ui.Button)]
    if expected:
        assert expected in texts and not buttons
    else:
        assert buttons == ["Show backups"]
