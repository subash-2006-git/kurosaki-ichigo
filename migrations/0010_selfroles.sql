-- Self-roles: members press buttons to give themselves pronoun and DM-status roles.
CREATE SCHEMA IF NOT EXISTS selfroles;

-- The role behind each option (created on first use, or an existing role with the same name).
CREATE TABLE selfroles.roles (
    guild_id  BIGINT NOT NULL,
    menu      TEXT NOT NULL,
    key       TEXT NOT NULL,
    role_id   BIGINT NOT NULL,
    PRIMARY KEY (guild_id, menu, key)
);

-- Where each picker message is posted, so it can be replaced.
CREATE TABLE selfroles.menus (
    guild_id    BIGINT NOT NULL,
    menu        TEXT NOT NULL,
    channel_id  BIGINT NOT NULL,
    message_id  BIGINT NOT NULL,
    PRIMARY KEY (guild_id, menu)
);
