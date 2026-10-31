-- Quote images: on by default; a server can turn them off from the panel.
CREATE SCHEMA IF NOT EXISTS quotes;

CREATE TABLE quotes.settings (
    guild_id  BIGINT PRIMARY KEY,
    enabled   BOOLEAN NOT NULL DEFAULT TRUE
);
