-- Per-server look (theme colour, footer) and where each server's control panel is posted.
CREATE TABLE core.guild_theme (
    guild_id     BIGINT PRIMARY KEY,
    accent       INTEGER,  -- RGB, NULL = default
    footer_text  VARCHAR(200),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE core.panels (
    guild_id    BIGINT PRIMARY KEY,
    channel_id  BIGINT NOT NULL,
    message_id  BIGINT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
