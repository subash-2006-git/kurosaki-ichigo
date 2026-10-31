-- Voice commands (!disconnect all, !server-mute, !server-deafen…): roles and members they skip.
CREATE SCHEMA IF NOT EXISTS voicecontrol;

CREATE TABLE voicecontrol.settings (
    guild_id         BIGINT PRIMARY KEY,
    exempt_role_ids  BIGINT[] NOT NULL DEFAULT '{}',
    exempt_user_ids  BIGINT[] NOT NULL DEFAULT '{}'
);
