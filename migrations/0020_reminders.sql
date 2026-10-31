-- Reminders: /remind me in:2h about:... pings the member when the time comes.
CREATE SCHEMA IF NOT EXISTS reminders;

CREATE TABLE reminders.reminders (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    guild_id    BIGINT,                 -- NULL when set in DMs
    channel_id  BIGINT,                 -- where to post it; DMs when private or the channel is gone
    text        TEXT NOT NULL,
    private     BOOLEAN NOT NULL DEFAULT FALSE,
    jump_url    TEXT,                   -- the confirmation message, linked from the reminder
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX reminders_due_idx ON reminders.reminders (due_at);
CREATE INDEX reminders_user_idx ON reminders.reminders (user_id);
