-- Anti-nuke: stop an account that mass-deletes channels/roles, mass-bans, spams webhooks or adds bots.
-- Off until a server turns it on. Snapshots of deleted channels/roles live in memory only.
CREATE SCHEMA IF NOT EXISTS antinuke;

CREATE TABLE antinuke.settings (
    guild_id                BIGINT PRIMARY KEY,
    enabled                 BOOLEAN NOT NULL DEFAULT FALSE,
    punishment              TEXT NOT NULL DEFAULT 'strip_roles' CHECK (punishment IN ('strip_roles', 'kick', 'ban')),
    limits                  JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {action: {count, seconds}}, merged with defaults
    block_bot_add           BOOLEAN NOT NULL DEFAULT TRUE,
    revert_dangerous_perms  BOOLEAN NOT NULL DEFAULT TRUE,
    restore                 BOOLEAN NOT NULL DEFAULT TRUE,
    trusted_user_ids        BIGINT[] NOT NULL DEFAULT '{}',
    trusted_role_ids        BIGINT[] NOT NULL DEFAULT '{}',
    alert_channel_id        BIGINT
);
