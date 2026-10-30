# kurosaki-ichigo

One discord.py bot (Python 3.11.9) that replaces four separate bots, sharing one Neon Postgres database:

| Feature | Came from | What it does |
|---|---|---|
| **moderation** | mod-bot | Bans, kicks, timeouts, warnings with automatic escalation, numbered cases, automod, anti-raid, reports, staff notes, role and channel tools |
| **logging** | logging-bot | Server event logs in a 📂 LOGGING category (14 channels), moderator attribution from the audit log, 📊 SERVER STATS live counters |
| **nickname** | nickname-bot | Template-based bulk renames, auto-rename on join, an embed builder |
| **community** | general-bot | Welcome/goodbye messages with image cards, message and voice XP, level roles, `/rank` cards, `/leaderboard` |

## Control panel (no commands needed)

Run **`/panel`** once in a staff channel (needs Manage Server). It posts a permanent panel listing every feature with its current status and an **Open** button:

| Section | What you can change |
|---|---|
| 👋 Welcome & Goodbye | on/off, channel, embed or plain text, image card, card background (upload or link), link buttons, goodbye DM, **Edit message** (embed editor with live preview), **Preview** |
| 📈 Leveling | on/off, level-up announcements and channel, level-up message and link buttons, message/voice XP rules, level roles and role mode |
| 🛡️ Moderation | mod-log / reports / alerts channels, punishment DMs, appeal link, automod filters and limits, banned words, allowed links, exemptions, escalation rules, anti-raid, raid mode |
| 📜 Server Logs | which log channels exist, create/repair log channels and stats counters, staff read access |
| 🏷️ Nicknames | mode, template, rename bots, excluded roles/members, preview / rename / restore, job status |
| 🎨 Appearance | the server's theme colour and footer, used by the bot's messages |

- Editors open **privately** (only you see them) and close after 10 minutes without use; every change applies instantly and updates the panel.
- The panel's buttons keep working after restarts. Only members with **Manage Server** can use them. Running `/panel` again replaces the old panel.
- Welcome, goodbye and level-up messages are embeds you design in the editor. Placeholders like `{user}`, `{server}`, `{membercount_ordinal}`, `{avatar}` and `{level}` are filled in; the image card shows inside the embed; up to 5 link buttons (Rules, Website…) go underneath.
- Level-up messages carry **📊 My rank** and **🏆 Leaderboard** buttons; mod-log cases carry **📜 History** and **↩️ Undo**; punishment DMs carry an **Appeal** button. All of these keep working after restarts.

All the slash commands below still work and change the same settings.

## Commands

All commands are slash commands. Admin commands are hidden from members who don't have the needed permission (Server Settings → Integrations can change that per server).

| Area | Commands |
|---|---|
| Bot | `/panel` (control panel) · `/ping` · `/status` (Manage Server: Discord + database latency, uptime, running jobs) · `/sync` (bot owner: re-sync commands) |
| Punish | `/warn` `/warnings` `/delwarn` `/clearwarns` · `/timeout` `/untimeout` · `/kick` · `/ban` `/tempban` `/softban` `/unban` |
| Cases | `/case` `/modlogs` `/reason` |
| Moderation setup | `/config view` · `/config modlog` `reports` `alerts` `appeal` `dm` · `/config automod …` · `/config badword …` · `/config whitelist …` · `/config escalation …` · `/config antiraid` · `/raidmode` |
| Staff tools | `/note add` `list` `delete` · `/role add` `remove` `temp` · `/purge` `/slowmode` `/lock` `/unlock` `/lockdown` · `/userinfo` `/serverinfo` |
| Reports | `/report` · right-click → Apps → **Report Message** / **Report User** (buttons in the report channel keep working after restarts) |
| Logging | `/setup logs [staff_role]` · `/setup stats` |
| Nicknames | `/nick template` `mode` `preview` `apply` `reset` `status` `stop` `settings` `exclude-role` `exclude-member` `include-bots` |
| Embeds | `/embed create` |
| Welcome / goodbye | `/welcome setup` `channel` `text` `gif` `card` `enable` `disable` `preview` `settings` · same for `/goodbye`, plus `/goodbye dm enable` `disable` `text` |
| Leveling | `/rank` · `/leaderboard` · `/leveling enable` `disable` `settings` `message-xp` `message-cooldown` `voice-xp` `voice-interval` `voice-rules` `levelup` `levelup-channel` `levelup-text` · `/leveling role add` `remove` `list` `mode` `sync` · `/leveling xp set` `add` `reset` `reset-all` |

## Discord application setup

1. [Developer Portal](https://discord.com/developers/applications) → your application → **Bot**:
   - Copy the token (`DISCORD_TOKEN`).
   - Turn on all three **Privileged Gateway Intents**: Presence, Server Members, Message Content. Then **Save Changes**.
     Logging needs all three; automod and edit/delete logs need Message Content.
2. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`, permission **Administrator** (simplest), and open the URL to invite the bot.
3. In each server, drag the bot's role **above** the roles it should moderate, rename, or give out. Discord won't let a bot act on anyone whose top role is above its own.
4. Privacy policy / terms URLs for the portal: link to [`PRIVACY.txt`](PRIVACY.txt) and [`TERMS.txt`](TERMS.txt) on GitHub (fill in the contact email at the bottom of both first).

## Run locally

```powershell
python -m venv venv            # with Python 3.11.9
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env         # then fill in DISCORD_TOKEN, DATABASE_URL, DEV_GUILD_ID
python main.py
```

- `DATABASE_URL`: the Neon **pooled** connection string (host contains `-pooler`). `sslmode`/`channel_binding` in the URL are fine.
- `DEV_GUILD_ID`: your test server's ID (Developer Mode on → right-click the server → Copy Server ID). Commands sync there instantly.
- Run **one copy** of the bot per token. If the hosted copy is running too, both answer every command ("interaction failed", double automod punishments).

On startup the bot binds `PORT` (default 8080), connects to Neon, applies any new migrations, loads every feature, syncs commands, then logs in. If Discord rate-limits the host's IP (Cloudflare error 1015, common on shared hosts), it waits with backoff instead of crash-looping.

## Deploy (Render)

A plain Python web service, no Docker needed:

- **Build**: `pip install -r requirements.txt` · **Start**: `python main.py` · **Health check path**: `/health`
- Python version: `.python-version` pins 3.11.9.
- Environment: `DISCORD_TOKEN`, `DATABASE_URL`, and `SYNC_COMMANDS=true` (global commands). Leave `DEV_GUILD_ID` empty in production.
- `render.yaml` describes the same service as a Blueprint, if you'd rather create it that way.

Switching from dev to production commands: guild copies synced while `DEV_GUILD_ID` was set stay in that server and show up twice next to the global ones. Run `/sync scope:clear-guild` there once to remove them.

`/health` returns 200 only when the bot is connected to Discord. It deliberately does not query the database, so uptime pingers don't keep Neon's compute awake; `/status` checks the database on demand.

## Environment variables

See [`.env.example`](.env.example). Only `DISCORD_TOKEN` and `DATABASE_URL` are required.

| Variable | Default | |
|---|---|---|
| `DISCORD_TOKEN` | — | required |
| `DATABASE_URL` | — | required, Neon pooled connection string |
| `DEV_GUILD_ID` | — | sync commands to one server (development) |
| `SYNC_COMMANDS` | `false` | sync global commands on startup (production) |
| `DATABASE_SSL` | `require` | `disable` only for a local Postgres without TLS |
| `PORT` | `8080` | web server (Render sets this) |
| `LOG_LEVEL` | `INFO` | |
| `STATS_UPDATE_INTERVAL` / `STATS_RECONCILE_INTERVAL` | `30` / `600` | stats counter refresh (seconds) |
| `RENAME_DELAY_SECONDS` | `1.0` | pause between renames in a bulk `/nick` job |

## Project layout

```
main.py              entry point: logging → web server → wait out rate limits → bot
core/                shared by every feature
  bot.py             the Bot class, intents, extension list, command sync, feature hooks
  config.py          every environment variable
  db.py              one asyncpg pool for everything (tuned for Neon)
  migrate.py         applies migrations/*.sql in order, once each
  errors.py          the one slash-command error handler
  interactions.py    reply()/defer() helpers and opt-in auto-defer
  ui/                shared look: style (colours, emoji), theme store, EmbedDraft and the embed editor
  panel/             /panel, the section registry, restart-proof Open buttons, editor building blocks, Appearance
  web.py             / and /health
  cogs/system.py     /ping, /status, /sync
features/
  moderation/  logging/  nickname/  community/     each: __init__.py (setup), cogs/, services or utils, db layer
migrations/          0001_core … 0007_community_messages (.sql)
assets/fonts/        Noto fonts for the image cards
tests/               pytest; tests/community uses an embedded Postgres
PRIVACY.txt TERMS.txt
```

Each feature keeps its tables in its own Postgres schema (`moderation.*`, `logging.*`, `nickname.*`, `community.*`), so table names never collide.

Features plug into core through small hooks on the bot instead of importing each other:

- `bot.error_reporters`: unexpected command/event errors are also posted to the logging feature's 🧩・custom-logs.
- `bot.status_fields`: lines a feature adds to `/status` (nickname shows its running job).
- `bot.join_filters`: anti-raid marks members it is kicking, so welcome/goodbye cards and nickname auto-renames skip them.
- `bot.panel.register(PanelSection(...))`: a feature's section on the control panel (each feature's `panel.py`).
- `bot.theme.brand(embed, guild)` / `core.ui.theme.brand`: give an embed the server's theme colour and footer.

### Changing the database

Add a new file `migrations/NNNN_description.sql` with the next number. Never edit a migration that has already run; the runner logs a warning if an applied file changes. Migrations run at startup inside one transaction, under a lock, so two instances starting together can't both apply them.

## Tests

```powershell
pip install -r requirements-dev.txt
pytest
```

About 500 tests. The community tests start an embedded Postgres ([pgserver](https://pypi.org/project/pgserver/)), give every test a fresh database with all migrations applied, and never touch Neon.
