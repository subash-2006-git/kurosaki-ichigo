-- Starboard: messages that get enough of a reaction (⭐ by default) are reposted to a highlights channel.
CREATE SCHEMA IF NOT EXISTS starboard;

CREATE TABLE starboard.settings (
    guild_id    BIGINT PRIMARY KEY,
    enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    channel_id  BIGINT,
    emoji       VARCHAR(100) NOT NULL DEFAULT '⭐',
    threshold   INTEGER NOT NULL DEFAULT 3 CHECK (threshold BETWEEN 1 AND 100),
    allow_self  BOOLEAN NOT NULL DEFAULT FALSE
);

-- One row per message on the board.
CREATE TABLE starboard.entries (
    message_id       BIGINT PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    channel_id       BIGINT NOT NULL,
    author_id        BIGINT NOT NULL,
    star_message_id  BIGINT NOT NULL,
    stars            INTEGER NOT NULL
);
CREATE INDEX entries_author_idx ON starboard.entries (author_id);
