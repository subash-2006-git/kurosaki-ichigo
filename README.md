# kurosaki-ichigo

One discord.py bot (Python 3.11.9) that replaces four separate bots, sharing one Neon Postgres database:

| Feature | Came from | What it does |
|---|---|---|
| **moderation** | mod-bot | Bans, kicks, timeouts, warnings with automatic escalation, numbered cases, automod, anti-raid, reports, staff notes, role and channel tools |
| **logging** | logging-bot | Server event logs in a 📂 LOGGING category (14 channels), moderator attribution from the audit log, 📊 SERVER STATS live counters |
| **nickname** | nickname-bot | Template-based bulk renames, auto-rename on join, an embed builder |
| **community** | general-bot | Welcome/goodbye messages with image cards, message and voice XP, level roles, `/rank` cards, `/leaderboard` |

## Control panel (no commands needed)

Run **`/panel`** once in a staff channel (needs Manage Server). It posts a permanent panel listing every feature with its current status, and a button per feature that opens its settings:

| Section | What you can change |
|---|---|
| 👋 Welcome & Goodbye | on/off, channel, embed or plain text, image card, card background (upload or link), link buttons, goodbye DM, **Edit message** (embed editor with live preview), **Preview** |
| 📈 Leveling | on/off, level-up announcements and channel, level-up message and link buttons, message/voice XP rules, **XP boost events** (2–5× XP for a set time), **no-XP channels** (channels or whole categories), level roles and role mode |
| 🏷️ Self roles | post the pronoun picker (several allowed) and the DM-status picker (open / closed / ask; one at a time), created on first use; pick up to 10 of your roles as **ping roles** and post that picker |
| ⭐ Starboard | on/off, channel, reaction and stars needed, whether self-stars count |
| 💡 Suggestions | on/off, suggestions channel, a discussion thread on each suggestion |
| 💬 Quote images | on/off, and the default look of new quotes (colour or grey avatar, side, light/dark, tinted with your theme colour) |
| 🎫 Tickets | on/off, category for ticket channels, staff roles, transcripts channel, open tickets per member, ask for a reason, **Edit ticket message**, post the ticket button; **types** (own button, staff roles and category); closing quiet tickets; ratings and their averages |
| 🧨 Anti-nuke | on/off, punishment (remove roles / kick / ban), undo the damage, block untrusted bots, block dangerous permissions, trusted people and roles, alerts channel, limits |
| 🛡️ Moderation | mod-log / reports / alerts channels, punishment DMs, appeal link, automod filters and limits, banned words, allowed links, exemptions, escalation rules and how long warnings count, anti-raid (join floods, young accounts: kick or alert), raid mode and its automatic end |
| 📜 Server Logs | which log channels exist, create/repair log channels and stats counters, staff read access |
| 🏷️ Nicknames | mode, template, rename bots, excluded roles/members, preview / rename / restore, job status |
| 🎨 Appearance | the server's theme colour and footer, used by the bot's messages |

- A **Getting started** checklist at the top shows what's left to set up (bot role at the top, logs, mod-log, anti-nuke with trusted admins, the ticket button) and shrinks to one line once everything is done.
- Editors open **privately** (only you see them) and close after 10 minutes without use; every change applies instantly and updates the panel.
- The panel's buttons keep working after restarts. Only members with **Manage Server** can use them. Running `/panel` again replaces the old panel.
- Welcome, goodbye and level-up messages are embeds you design in the editor. Placeholders like `{user}`, `{server}`, `{membercount_ordinal}`, `{avatar}` and `{level}` are filled in; the image card shows inside the embed; up to 5 link buttons (Rules, Website…) go underneath.
- **Suggestions:** `/suggest` posts the idea in the suggestions channel with 👍/👎 buttons (press again to take a vote back) and **Accept**/**Deny** buttons for staff with Manage Messages, who can add a reason. The member gets a DM with the decision, and voting closes. One suggestion per member per minute.
- Level-up messages carry **📊 My rank** and **🏆 Leaderboard** buttons; mod-log cases carry **📜 History** and **↩️ Undo**; punishment DMs carry an **Appeal** button. All of these keep working after restarts.

Settings commands the panel replaces (`/config`, `/welcome`, `/goodbye`, `/setup`, and the settings parts of `/leveling` and `/nick`) are hidden to keep the command list short; set `CONFIG_COMMANDS=true` to bring them back. The commands below are the ones that stay.

### Self roles, starboard and quote images

- **Self roles:** post the pickers from the panel. Members press buttons to add or remove roles: pronouns (he/him, she/her, they/them, any pronouns, ask my pronouns; pick several) and DM status (DMs open, DMs closed, ask to DM; one at a time). Roles are created the first time someone picks them, or an existing role with the same name is used. **Ping roles:** pick up to 10 of your own roles (announcements, events, …) in the panel and post that picker too; members opt in and out, and the posted picker updates when you change the roles.
- **Starboard:** when a message gets enough ⭐ (or your chosen reaction), it's reposted to the starboard channel with the author, text, first image and a Jump button. The count updates, and the post leaves if stars drop below the limit. Self-stars don't count unless you allow them; bot messages are ignored.
- **Quote images:** right-click a message → **Apps → Make a quote image**, reply to a message and mention the bot, or use **`/quote`** (latest message here, `user:` for someone's latest, or `message:` with a message link or ID; slash commands can't be sent as replies, so a link picks a specific message). Add **`count:`** (2–5) to put several messages on one image as a conversation: the latest ones, or from the linked message onwards. The picture shows the author's avatar fading into their words. Buttons switch the avatar between colour and grey, flip sides, switch light/dark and tint the background with your theme colour; the requester, the quoted person or anyone with Manage Messages can change or delete it. Turn it off in the panel.
- **`/mydata`** (anyone): shows what the bot stores about you across all servers and lets you delete your XP, levels, nickname records and starboard posts. Moderation and ticket records are kept, since servers need them to enforce their rules.

### How anti-nuke works

A "nuke" is a hacked admin account or a malicious bot wrecking a server in seconds: deleting channels and roles, mass-banning, spamming webhooks, or handing out Administrator. Anti-nuke is **off until you turn it on** in the panel.

1. **Watches the audit log.** Every channel/role delete or create, ban, kick, webhook create, bot add and role permission change arrives with *who* did it.
2. **Counts per person.** If one person does more than the limit within the time window (default: more than 3 channel or role deletes, bans, kicks or webhooks in 10 seconds; more than 5 channel or role creates), they're stopped.
3. **Instant blocks.** A bot added by someone untrusted is kicked; a role given Administrator, Ban, Kick, Manage Server/Roles/Channels/Webhooks or Mention Everyone by someone untrusted is reverted.
4. **Stops the attacker.** Removes all their roles (default), or kicks or bans them. Bots that misbehave are kicked.
5. **Undoes the damage.** Recreates the channels and roles they deleted (name, category, position, permissions, topic, colour), deletes channels, roles and webhooks they created, and unbans who they banned.
6. **Alerts** the alerts channel (or the moderation alerts / mod-log channel) and pings the owner.

Bans and kicks that moderators do **through the bot's commands** (`/ban`, `/kick`, `/tempban`, `/softban`, report buttons) count against that moderator too, even though Discord's audit log credits them to the bot. Automod and warning escalation act as the bot and aren't counted.

The server owner and the bot are always trusted. **Add your admins as trusted people or roles**, otherwise someone legitimately reorganising lots of channels can be stopped. Only the owner or an Administrator can change anti-nuke settings.

**Limits (true for every anti-nuke bot):** Discord has no way to block an action before it happens, so actions are caught and reversed right after. Messages inside deleted channels can't be brought back. The bot can't stop anyone whose highest role is above its own (the panel warns when the bot's role isn't at the top) and can never stop the server owner. Restored roles aren't automatically re-given to the members who had them.

### Tickets

Set up from the panel's 🎫 Tickets section, then post the ticket button in a public channel. A member presses **🎫 Open a ticket** (optionally giving a reason) and gets a private `ticket-0001` channel that only they, your staff roles and the bot can see. Staff press **🙋 Claim** to take it; staff or the member press **🔒 Close**: a `.txt` transcript goes to the transcripts channel and to the member, then the channel is deleted. All buttons keep working after restarts. The bot needs **Manage Channels** (and Manage Roles, to set the ticket channel permissions).

- **Types** (up to 5): each gets its own button on the ticket message, can add its own staff roles, and can use its own category.
- **Closing quiet tickets** (off by default): after the chosen hours without messages the member is pinged with a warning; if nobody writes before the warning runs out, the ticket closes with a transcript as usual.
- **Ratings** (on by default): the closing DM carries 1–5 ⭐ buttons. Each rating is posted in the transcripts channel, and the panel shows the average overall and per staff member (by who claimed the ticket).

## Commands

All commands are slash commands. Admin commands are hidden from members who don't have the needed permission (Server Settings → Integrations can change that per server).

| Area | Commands |
|---|---|
| Bot | `/panel` (control panel) · `/quote` (quote image) · `/suggest` (post a suggestion) · `/mydata` (anyone: see or delete your data) · `/ping` · `/status` (Manage Server: Discord + database latency, uptime, running jobs) · `/sync` (bot owner: re-sync commands) |
| Punish | `/warn` `/warnings` `/delwarn` `/clearwarns` · `/timeout` `/untimeout` · `/kick` · `/ban` `/tempban` `/softban` `/unban` |
| Cases | `/case` `/modlogs` `/reason` |
| Raids | `/raidmode` |
| Staff tools | `/note add` `list` `delete` · `/role add` `remove` `temp` · `/purge` `/slowmode` `/lock` `/unlock` `/lockdown` · `/userinfo` `/serverinfo` |
| Reports | `/report` · right-click → Apps → **Report Message** / **Report User** (buttons in the report channel keep working after restarts) |
| Nicknames | `/nick preview` `apply` `reset` `status` `stop` |
| Embeds | `/embed create` |
| Leveling | `/rank` · `/leaderboard` · `/leveling xp set` `add` `reset` `reset-all` · `/leveling role sync` |
| Settings | in `/panel` (with `CONFIG_COMMANDS=true` also `/config …`, `/welcome …`, `/goodbye …`, `/setup …` and the settings parts of `/leveling` and `/nick`) |

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

## Backups

`.github/workflows/backup.yml` dumps the bot's schemas every day at 03:00 UTC (and on demand: **Actions → Database backup → Run workflow**), encrypts the dump, and keeps it as a workflow artifact for 30 days. The repo is public, so backups are always encrypted.

**Setup (once):** GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

- `DATABASE_URL`: the same Neon connection string the bot uses.
- `BACKUP_PASSPHRASE`: a long random passphrase. **Store a copy somewhere safe**: without it a backup can't be read, and with it anyone holding the file can.

**Restore:** download the artifact from the workflow run, unzip it, then (with an empty database, e.g. a new Neon branch):

```bash
export BACKUP_PASSPHRASE='...'
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in backup-….sql.gz.enc   | gunzip | psql "postgresql://…direct (non-pooler) Neon URL…"
```

Use a `psql` from Postgres 18 or newer (Neon's version). Restoring into the live database would clash with existing rows; restore into a fresh branch and point `DATABASE_URL` at it.

## Environment variables

See [`.env.example`](.env.example). Only `DISCORD_TOKEN` and `DATABASE_URL` are required.

| Variable | Default | |
|---|---|---|
| `DISCORD_TOKEN` | — | required |
| `DATABASE_URL` | — | required, Neon pooled connection string |
| `DEV_GUILD_ID` | — | sync commands to one server (development) |
| `SYNC_COMMANDS` | `false` | sync global commands on startup (production) |
| `CONFIG_COMMANDS` | `false` | `true` keeps the settings commands the panel replaces (`/config`, `/welcome`, `/goodbye`, `/setup`, most of `/leveling` and `/nick`) |
| `OWNER_IDS` | — | user IDs (comma-separated) allowed to use `/sync`; empty = the app's owner or team |
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
  moderation/  logging/  nickname/  community/  tickets/  antinuke/  selfroles/  starboard/  quotes/  privacy/     each: __init__.py (setup), cogs/, services or utils, db layer
migrations/          0001_core … 0012_quotes (.sql)
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

GitHub Actions runs the whole suite on every push (`.github/workflows/tests.yml`). To make Render deploy only commits that pass, set the service's **Auto-Deploy** to **After CI checks pass**.

```powershell
pip install -r requirements-dev.txt
pytest
```

About 500 tests. The community tests start an embedded Postgres ([pgserver](https://pypi.org/project/pgserver/)), give every test a fresh database with all migrations applied, and never touch Neon.
