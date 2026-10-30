-- Logging feature: remembers the logging/stats categories and channels by ID, so admins can
-- rename or move them freely. Keys: 'category:logs', 'category:stats', 'log:<key>', 'stat:<key>'.
CREATE SCHEMA IF NOT EXISTS logging;

CREATE TABLE logging.channels (
    guild_id    BIGINT NOT NULL,
    key         TEXT NOT NULL,
    channel_id  BIGINT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, key)
);
CREATE INDEX channels_channel_idx ON logging.channels (channel_id);
