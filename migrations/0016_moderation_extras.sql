-- Warnings that stop counting after a while, and when raid mode was turned on (for its automatic end).
ALTER TABLE moderation.guild_config
    ADD COLUMN warn_expiry_days INTEGER NOT NULL DEFAULT 0 CHECK (warn_expiry_days BETWEEN 0 AND 365),
    ADD COLUMN raidmode_since TIMESTAMPTZ;
