-- Welcome, goodbye and level-up messages as configurable embeds with link buttons.
-- *_embed: the stored EmbedDraft (NULL = the built-in default); *_buttons: [{label, url, emoji}];
-- *_use_embed: FALSE keeps the old plain-text message (the *_text columns).
ALTER TABLE community.guild_settings
    ADD COLUMN welcome_embed       JSONB,
    ADD COLUMN welcome_buttons     JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN welcome_use_embed   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN goodbye_embed       JSONB,
    ADD COLUMN goodbye_buttons     JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN goodbye_use_embed   BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN levelup_embed       JSONB,
    ADD COLUMN levelup_buttons     JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN levelup_use_embed   BOOLEAN NOT NULL DEFAULT TRUE;
