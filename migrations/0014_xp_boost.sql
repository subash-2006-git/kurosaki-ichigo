-- Leveling: XP boost events (e.g. double XP for a weekend) and channels that never earn XP.
ALTER TABLE community.guild_settings
    ADD COLUMN xp_boost_multiplier  INTEGER NOT NULL DEFAULT 1 CHECK (xp_boost_multiplier BETWEEN 1 AND 5),
    ADD COLUMN xp_boost_until       TIMESTAMPTZ,
    ADD COLUMN no_xp_channel_ids    BIGINT[] NOT NULL DEFAULT '{}';
