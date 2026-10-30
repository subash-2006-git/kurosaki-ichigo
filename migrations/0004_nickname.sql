-- Nickname feature (ported from nickname-bot's SQLite schema).
-- Times are TIMESTAMPTZ, JSON text is JSONB/BIGINT[], IDs are BIGINT identity columns.
CREATE TABLE nickname.guild_settings (
    guild_id          BIGINT PRIMARY KEY,
    mode              TEXT NOT NULL DEFAULT 'off' CHECK (mode IN ('off', 'new', 'existing', 'both')),
    template          TEXT,
    include_bots      BOOLEAN NOT NULL DEFAULT FALSE,
    excluded_roles    BIGINT[] NOT NULL DEFAULT '{}',
    excluded_members  BIGINT[] NOT NULL DEFAULT '{}'
);

-- What a member's nickname was before the bot first changed it, and what the bot set it to.
-- Used for /nick reset and to avoid stacking prefixes.
CREATE TABLE nickname.original_nicknames (
    guild_id       BIGINT NOT NULL,
    user_id        BIGINT NOT NULL,
    original_nick  TEXT,
    applied_nick   TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE nickname.jobs (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    kind         TEXT NOT NULL,
    started_by   BIGINT NOT NULL,
    status       TEXT NOT NULL,
    total        INTEGER NOT NULL,
    processed    INTEGER NOT NULL DEFAULT 0,
    changed      INTEGER NOT NULL DEFAULT 0,
    skipped      JSONB NOT NULL DEFAULT '{}'::jsonb,
    failed       JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ
);
CREATE INDEX jobs_guild_idx ON nickname.jobs (guild_id, id);
-- Startup marks running jobs interrupted; keep that UPDATE from scanning every job.
CREATE INDEX jobs_running_idx ON nickname.jobs (status) WHERE status = 'running';

CREATE TABLE nickname.change_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    job_id      BIGINT,
    source      TEXT NOT NULL,
    old_nick    TEXT,
    new_nick    TEXT,
    result      TEXT NOT NULL,
    reason      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX change_log_guild_idx ON nickname.change_log (guild_id, created_at);
