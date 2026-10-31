-- Invite tracking: which invite (and whose) each member joined with, for /invites counts.
CREATE SCHEMA IF NOT EXISTS invites;

CREATE TABLE invites.joins (
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    inviter_id  BIGINT,                     -- NULL when it couldn't be told (vanity URL, Discovery, …)
    code        TEXT,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)         -- the latest join; rejoining doesn't count twice
);
CREATE INDEX invites_inviter_idx ON invites.joins (guild_id, inviter_id);
CREATE INDEX invites_user_idx ON invites.joins (user_id);
