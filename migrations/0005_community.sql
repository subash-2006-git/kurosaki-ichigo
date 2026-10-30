-- Community feature: welcome/goodbye, leveling (ported from general-bot's SQLAlchemy models,
-- Alembic revisions 0001 and 0002). Text defaults match features/community/models.py.
CREATE TABLE community.guild_settings (
    guild_id               BIGINT PRIMARY KEY,

    welcome_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    welcome_channel_id     BIGINT,
    welcome_text           VARCHAR(1000) NOT NULL DEFAULT 'Welcome {user} to **{server}**! You''re our {membercount_ordinal} member.',
    welcome_gif_url        VARCHAR(500),
    welcome_card_enabled   BOOLEAN NOT NULL DEFAULT TRUE,

    goodbye_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    goodbye_channel_id     BIGINT,
    goodbye_text           VARCHAR(1000) NOT NULL DEFAULT 'Goodbye **{username}**. Thanks for being part of {server}.',
    goodbye_gif_url        VARCHAR(500),
    goodbye_card_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    goodbye_dm_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    goodbye_dm_text        VARCHAR(1000) NOT NULL DEFAULT 'Thanks for being part of {server}. You''re welcome back any time!',

    leveling_enabled       BOOLEAN NOT NULL DEFAULT FALSE,
    levelup_enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    levelup_channel_id     BIGINT,
    levelup_text           VARCHAR(500) NOT NULL DEFAULT '🎉 {user} reached **Level {level}**!',

    message_xp_min         INTEGER NOT NULL DEFAULT 5,
    message_xp_max         INTEGER NOT NULL DEFAULT 15,
    message_xp_cooldown    INTEGER NOT NULL DEFAULT 30,

    voice_xp_amount        INTEGER NOT NULL DEFAULT 5,
    voice_xp_interval      INTEGER NOT NULL DEFAULT 60,
    voice_require_others   BOOLEAN NOT NULL DEFAULT TRUE,
    voice_min_session      INTEGER NOT NULL DEFAULT 120,
    voice_ignore_muted     BOOLEAN NOT NULL DEFAULT FALSE,
    voice_ignore_deafened  BOOLEAN NOT NULL DEFAULT TRUE,

    level_role_mode        VARCHAR(16) NOT NULL DEFAULT 'highest' CHECK (level_role_mode IN ('highest', 'keep_all')),

    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE community.member_levels (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    user_id          BIGINT NOT NULL,
    total_xp         INTEGER NOT NULL DEFAULT 0,
    message_xp       INTEGER NOT NULL DEFAULT 0,
    voice_xp         INTEGER NOT NULL DEFAULT 0,
    -- Cache of level_for_xp(total_xp); only the leveling service writes it.
    level            INTEGER NOT NULL DEFAULT 0,
    last_known_name  VARCHAR(100),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (guild_id, user_id)
);
CREATE INDEX member_levels_guild_xp_idx ON community.member_levels (guild_id, total_xp DESC);

CREATE TABLE community.level_roles (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    level       INTEGER NOT NULL,
    -- Null means "create the role the first time someone reaches this level".
    role_id     BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (guild_id, level)
);

CREATE TABLE community.xp_log (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    source      VARCHAR(16) NOT NULL CHECK (source IN ('message', 'voice', 'admin')),
    amount      INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX xp_log_guild_user_created_idx ON community.xp_log (guild_id, user_id, created_at);
CREATE INDEX xp_log_created_idx ON community.xp_log (created_at);

-- Card background images, stored in the database so they survive hosts with no persistent disk.
CREATE TABLE community.guild_media (
    guild_id    BIGINT NOT NULL,
    kind        VARCHAR(16) NOT NULL CHECK (kind IN ('welcome', 'goodbye')),
    data        BYTEA NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, kind)
);
