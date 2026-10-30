-- Moderation feature (ported from mod-bot db/schema.sql).
CREATE TABLE IF NOT EXISTS moderation.guild_config (
    guild_id             BIGINT PRIMARY KEY,
    modlog_channel_id    BIGINT,
    report_channel_id    BIGINT,
    alert_channel_id     BIGINT,
    appeal_url           TEXT,
    dm_on_action         BOOLEAN NOT NULL DEFAULT TRUE,
    automod              JSONB NOT NULL DEFAULT '{}'::jsonb,
    raid                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    raidmode_active      BOOLEAN NOT NULL DEFAULT FALSE,
    locked_channels      BIGINT[] NOT NULL DEFAULT '{}',
    case_counter         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS moderation.cases (
    id                SERIAL PRIMARY KEY,
    guild_id          BIGINT NOT NULL,
    case_num          INTEGER NOT NULL,
    user_id           BIGINT NOT NULL,
    mod_id            BIGINT NOT NULL,
    action            TEXT NOT NULL,
    reason            TEXT,
    duration_seconds  BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (guild_id, case_num)
);
CREATE INDEX IF NOT EXISTS cases_guild_user_idx ON moderation.cases (guild_id, user_id);

CREATE TABLE IF NOT EXISTS moderation.warn_thresholds (
    guild_id          BIGINT NOT NULL,
    warn_count        INTEGER NOT NULL,
    action            TEXT NOT NULL,
    duration_seconds  BIGINT,
    PRIMARY KEY (guild_id, warn_count)
);

CREATE TABLE IF NOT EXISTS moderation.scheduled_actions (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    type        TEXT NOT NULL,
    role_id     BIGINT,
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS scheduled_actions_expires_idx ON moderation.scheduled_actions (expires_at);

CREATE TABLE IF NOT EXISTS moderation.notes (
    id          SERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    author_id   BIGINT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS notes_guild_user_idx ON moderation.notes (guild_id, user_id);

CREATE TABLE IF NOT EXISTS moderation.banned_words (
    guild_id  BIGINT NOT NULL,
    word      TEXT NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS moderation.link_whitelist (
    guild_id  BIGINT NOT NULL,
    domain    TEXT NOT NULL,
    PRIMARY KEY (guild_id, domain)
);

CREATE TABLE IF NOT EXISTS moderation.reports (
    id            SERIAL PRIMARY KEY,
    guild_id      BIGINT NOT NULL,
    reporter_id   BIGINT NOT NULL,
    target_id     BIGINT NOT NULL,
    message_link  TEXT,
    reason        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    handled_by    BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
