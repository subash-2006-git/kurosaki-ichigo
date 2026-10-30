# kurosaki-ichigo — merge plan

One discord.py bot (Python 3.11.9) that replaces five bots, backed by one Neon Postgres database.

| Source bot | Feature package | Has DB today |
|---|---|---|
| general-bot | `features/community` (welcome, goodbye, leveling, voice XP, leaderboard, cards) | SQLAlchemy + Alembic (SQLite/Supabase) |
| logging-bot | `features/logging` (event logs, audit attribution, stats counters) | none |
| mod-bot | `features/moderation` (cases, warns, automod, anti-raid, reports, notes, roles, channel tools) | asyncpg on Neon |
| music-bot | `features/music` (yt-dlp player, buttons, auto-update) | none |
| nickname-bot | `features/nickname` (bulk nickname jobs, `/embed` builder) | sync sqlite3 |

Not included: user-bot, spam-bot. The original folders are left untouched; they are only read as a reference.

---

## 1. Layout

```
kurosaki-ichigo/
  main.py                    # entry: logging -> web server -> bot.start()
  core/
    bot.py                   # the single commands.Bot subclass
    config.py                # every env var in one place
    db.py                    # one asyncpg pool for every feature
    migrate.py               # runs migrations/*.sql in order, tracked in core.schema_migrations
    web.py                   # one aiohttp server: / /health /privacy /terms
    legal.py                 # combined privacy policy and terms of service
    errors.py                # one app-command error handler + bot on_error
    interactions.py          # reply()/defer() helpers (from general-bot)
    logging_setup.py
  features/
    community/  cogs/ services/ repos.py
    logging/    cogs/ services/
    moderation/ cogs/ utils/ repos.py
    music/      cogs/ player/
    nickname/   cogs/ services/ repos.py
  migrations/
    0001_core.sql  0002_community.sql  0003_moderation.sql  0004_nickname.sql
  assets/fonts/              # copied from general-bot
  tests/
  requirements.txt  Dockerfile  start.sh  render.yaml  .env.example  README.md
```

Each feature gets its own package namespace, which avoids the `config`, `utils`, `bot`, and `cogs.setup` module-name clashes between the bots.

## 2. Database (one Neon DB)

**Driver:** asyncpg everywhere, with a single pool on `bot.db`, built on mod-bot's `Database` because it is already tuned for Neon:
- `min_size=0` and short idle lifetime, so Neon can scale to zero
- `statement_cache_size=0` for the `-pooler` endpoint
- `sslmode` and `channel_binding` stripped from the DSN, with `ssl=require`
- JSONB codec

Why asyncpg instead of SQLAlchemy: mod-bot is already asyncpg, and nickname has to be rewritten anyway. Only general-bot's repositories (~230 lines) need porting from SQLAlchemy. That beats running two DB stacks against one database.

**Namespacing:** each feature gets its own Postgres schema, so the generic table names stop colliding:

| Schema | Tables (from) |
|---|---|
| `core` | `schema_migrations` |
| `community` | `guild_settings`, `member_levels`, `level_roles`, `xp_log`, `guild_media` (general-bot) |
| `moderation` | `guild_config`, `cases`, `warn_thresholds`, `scheduled_actions`, `notes`, `banned_words`, `link_whitelist`, `reports` (mod-bot) |
| `nickname` | `guild_settings`, `original_nicknames`, `jobs`, `change_log` (nickname-bot) |

Logging and music have no tables.

**Migrations:** plain SQL files applied at startup inside a transaction holding an advisory lock, so two instances during a Render deploy can't both run them. This replaces general-bot's Alembic setup and mod-bot's run-`schema.sql`-every-boot approach.

**Type changes for nickname:**
- `AUTOINCREMENT` becomes `BIGINT GENERATED ALWAYS AS IDENTITY`
- epoch `REAL` becomes `TIMESTAMPTZ`
- JSON `TEXT` becomes `JSONB`
- IDs become `BIGINT`
- `?` placeholders become `$n`

**`on_guild_remove`:** nickname deletes only its own `nickname.*` rows. General-bot kept data and mod-bot never deleted, so their behaviour is unchanged.

## 3. Bot-wide settings (merged)

| Setting | Merged value | Why |
|---|---|---|
| Intents | default + `members` + `presences` + `message_content` + `voice_states` | union: logging needs all three privileged intents, automod needs content |
| Prefix | `commands.when_mentioned`, `help_command=None` | every bot was slash-only |
| `allowed_mentions` | everyone=False, roles=False, users=True | log router sends with `AllowedMentions.none()` explicitly; embed builder and raid alerts already override per message |
| Command tree | plain `CommandTree` plus **opt-in** auto-defer via `extras={"defer": True}` | general-bot's tree deferred every command, which would break modals and direct responses in the other features |
| Sync | `DEV_GUILD_ID` → guild sync; else `SYNC_COMMANDS=true` → global sync; else none. Plus owner-only `/sync` | avoids global sync on every boot (rate limits) |
| Error handling | one `tree.on_error`: union of the friendly messages from all bots; unexpected errors also go to the logging feature's custom-logs channel if set up | each bot replaced the global handler, so only one could survive |
| `on_ready` | ensure `community.guild_settings` rows, rebuild voice sessions and invite cache, post logging startup notice (once per process) | merged from the subclasses |
| Presence | none by default, configurable via env | each bot set its own |
| Logging | `setup_logging` that does **not** silence non-`bot.*` loggers | general-bot's setup hid everything else |

## 4. Commands — collisions and resolution

Only `/ping` actually collides; it exists in logging, nickname, and general.

- **`/ping`** (core, anyone): latency and uptime.
- **`/status`** (core, Manage Server): gateway and DB latency, uptime, guilds, running nickname job, active voice players, commit. This merges general-bot's `/status` with nickname-bot's extended ping.
- **`/sync`** (core, owner): from mod-bot.

Every other command keeps its original name and permissions:

| Feature | Commands |
|---|---|
| community | `/rank`, `/leaderboard`, `/welcome …`, `/goodbye …`, `/leveling …` |
| logging | `/setup logs`, `/setup stats` |
| moderation | `/ban`, `/tempban`, `/softban`, `/unban`, `/kick`, `/timeout`, `/untimeout`, `/warn`, `/warnings`, `/delwarn`, `/clearwarns`, `/userinfo`, `/serverinfo`, `/case`, `/modlogs`, `/reason`, `/purge`, `/slowmode`, `/lock`, `/unlock`, `/lockdown`, `/role …`, `/note …`, `/raidmode`, `/report`, context menus *Report Message* and *Report User*, `/config …` |
| music | `/play`, `/pause`, `/resume`, `/skip`, `/stop`, `/leave`, `/nowplaying`, `/volume`, `/queue`, `/remove`, `/shuffle`, `/clear`, `/loop` |
| nickname | `/nick …`, `/embed create` |

That is about 50 top-level commands, under Discord's limit of 100.

`/config` is moderation-only but reads as bot-wide. I'll leave it as-is unless you want it renamed.

## 5. Cross-feature interactions to handle

- **Mod actions vs logging:**
  - The two feeds are different. The mod-log is case-numbered; the logging feature records audit events.
  - Mod actions show up in both, which is intended.
  - Actions from the moderation feature carry the `"Mod (id): reason"` audit reason, so the logging embed shows the real moderator instead of the bot.
- **Automod deletions** fire the logging feature's message-delete log. That is kept, because it is useful evidence.
- **Shared voice client:**
  - Music is the only feature that joins voice. Voice XP and logging only *observe* voice state, so there is no conflict.
  - Logging's voice-session log will show the bot itself joining and leaving for music. Bots are already filtered in voice XP; the logging feature will skip the bot itself.
- **`on_member_join`** runs in four listeners: welcome, level-role restore, nickname auto-rename, and anti-raid. If anti-raid kicks the member, the other three skip them, using a `member.guild.get_member` check after a short yield.
- **Stats counters vs channel-update logs:** the logging feature already skips its own counter renames.
- **Music auto-update:**
  - yt-dlp self-update restarts the *whole* bot. This happens only when no voice is active, and is gated by `AUTO_UPDATE`.
  - It stays on in Docker.
  - The cost is a short outage of every feature, roughly weekly.

## 6. Web server, legal pages, and deploy

- **Web server:** one aiohttp server on `$PORT`.
  - `/health` returns 200 only when the gateway is ready. It does **not** query the DB, because uptime pingers would keep Neon awake 24/7; `/status` does the DB check.
  - It reports guilds, latency, uptime, and voice connections.
- **Legal pages:** `/privacy` and `/terms` are rewritten to cover every feature honestly:
  - message content is read (automod, logs)
  - XP log retention is 30 days
  - DB is Neon, host is Render
  - nickname data is deleted on guild removal
  - bot name comes from `BOT_NAME`; nothing is hard-coded
- **Deploy:**
  - **Docker** is required by music: `python:3.11.9-slim` + ffmpeg + deno.
  - `start.sh` upgrades yt-dlp, then runs `python main.py`. Exit code 3 means restart.
  - `render.yaml` defines a single web service with `healthCheckPath: /health`, and the cookies file is a Render Secret File.
  - Without a persistent disk, Render's zero-downtime deploy can briefly run two gateway sessions. The migration advisory lock covers the DB side of that; duplicated events during the overlap are accepted.

## 7. Environment variables

| Var | Default | Used by |
|---|---|---|
| `DISCORD_TOKEN` | required | core |
| `DATABASE_URL` | required (Neon) | core |
| `DATABASE_SSL` | `require` | core |
| `PORT` | `8080` | core |
| `LOG_LEVEL` | `INFO` | core |
| `DEV_GUILD_ID` | — | core |
| `SYNC_COMMANDS` | `false` | core |
| `BOT_NAME` | — | core (legal pages) |
| `CONTACT_EMAIL` | — | core (legal pages) |
| `STATS_UPDATE_INTERVAL` | 30 | logging |
| `STATS_RECONCILE_INTERVAL` | 600 | logging |
| `RENAME_DELAY_SECONDS` | 1.0 | nickname |
| `DEFAULT_VOLUME` | 50 | music |
| `IDLE_TIMEOUT` | 300 | music |
| `ALONE_TIMEOUT` | 60 | music |
| `MAX_QUEUE` | 200 | music |
| `YTDL_COOKIES_FILE` | — | music |
| `AUTO_UPDATE` | — | music |

Token var names differed (`DISCORD_TOKEN` vs `DISCORD_BOT_TOKEN`). The merged bot reads `DISCORD_TOKEN` only.

## 8. Build order

Each step ends with the bot booting and the new feature checked in a test server.

1. **Core:** config, db pool, migration runner, web server, error handler, sync, `/ping`, `/status`, `/sync`. Then `0001_core.sql`.
2. **Moderation:** smallest port, since it is already asyncpg. Table refs move to `moderation.*`, and `bot.configs` becomes `bot.mod_configs`.
3. **Logging:** no DB. Copy the code, rewire `bot.log_router` / `bot.audit`, and drop its `/ping`, `on_error`, and `tree.on_error`.
4. **Music:** copy the code, move the updater hook into core `main.py`, add the Dockerfile.
5. **Nickname:** rewrite `database.py` on the shared pool (async), and update `Job.from_row`.
6. **Community:** rewrite the SQLAlchemy repositories as asyncpg SQL, switch to opt-in defer, copy the fonts.
7. **Finish:** legal pages, README, `.env.example`, `render.yaml`, and the pure-logic tests ported. The tests cover cards, the leveling math, the nickname planner, embed_draft, and time parsing.

## 9. Decisions (2026-10-30)

- **Fresh database** — new Neon project, no data copied from the old bots.
- **New bot application/token** — the old bots keep their own tokens.

## 10. Progress

- [x] Phase 0 — token, intents, test guild, Neon connectivity verified
- [x] Phase 1 — core (config, pool, migrations, web, errors, /ping /status /sync)
- [ ] Phase 2 — moderation
- [ ] Phase 3 — logging
- [ ] Phase 4 — music
- [ ] Phase 5 — nickname
- [ ] Phase 6 — community
- [ ] Phase 7 — legal, README, deploy files, tests
