-- Suggestions: members post ideas with /suggest; everyone votes with buttons; staff accept or deny.
CREATE SCHEMA IF NOT EXISTS suggestions;

CREATE TABLE suggestions.settings (
    guild_id    BIGINT PRIMARY KEY,
    enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    channel_id  BIGINT,
    threads     BOOLEAN NOT NULL DEFAULT FALSE,  -- open a discussion thread on each suggestion
    counter     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE suggestions.suggestions (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id         BIGINT NOT NULL,
    number           INTEGER NOT NULL,
    author_id        BIGINT NOT NULL,
    channel_id       BIGINT NOT NULL,
    message_id       BIGINT,
    content          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'denied')),
    decided_by       BIGINT,
    decision_reason  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at       TIMESTAMPTZ,
    UNIQUE (guild_id, number)
);
CREATE INDEX suggestions_author_idx ON suggestions.suggestions (author_id);

CREATE TABLE suggestions.votes (
    suggestion_id  BIGINT NOT NULL REFERENCES suggestions.suggestions (id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL,
    vote           SMALLINT NOT NULL CHECK (vote IN (-1, 1)),
    PRIMARY KEY (suggestion_id, user_id)
);
CREATE INDEX suggestion_votes_user_idx ON suggestions.votes (user_id);
