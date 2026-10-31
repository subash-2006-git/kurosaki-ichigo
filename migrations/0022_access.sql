-- Per-server access: the members (besides the server owner) who can use the bot's commands and panel.
CREATE TABLE core.access (
    guild_id  BIGINT PRIMARY KEY,
    user_ids  BIGINT[] NOT NULL DEFAULT '{}'
);
