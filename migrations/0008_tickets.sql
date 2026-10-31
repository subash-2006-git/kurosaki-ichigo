-- Tickets: members press a button to open a private channel with the staff team.
CREATE SCHEMA IF NOT EXISTS tickets;

CREATE TABLE tickets.settings (
    guild_id          BIGINT PRIMARY KEY,
    enabled           BOOLEAN NOT NULL DEFAULT FALSE,
    category_id       BIGINT,                         -- where ticket channels are created
    staff_role_ids    BIGINT[] NOT NULL DEFAULT '{}', -- can see, claim and close every ticket
    log_channel_id    BIGINT,                         -- transcripts of closed tickets
    panel_channel_id  BIGINT,                         -- where the "Open a ticket" message is posted
    panel_message_id  BIGINT,
    max_open          INTEGER NOT NULL DEFAULT 1 CHECK (max_open BETWEEN 1 AND 10),
    ask_reason        BOOLEAN NOT NULL DEFAULT TRUE,
    opening_embed     JSONB,                          -- NULL = the built-in default
    button_label      VARCHAR(80) NOT NULL DEFAULT 'Open a ticket',
    counter           INTEGER NOT NULL DEFAULT 0      -- last ticket number used
);

CREATE TABLE tickets.tickets (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    number      INTEGER NOT NULL,
    channel_id  BIGINT NOT NULL,
    opener_id   BIGINT NOT NULL,
    claimed_by  BIGINT,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at   TIMESTAMPTZ,
    closed_by   BIGINT,
    UNIQUE (guild_id, number)
);
CREATE INDEX tickets_channel_idx ON tickets.tickets (channel_id);
CREATE INDEX tickets_open_idx ON tickets.tickets (guild_id, opener_id) WHERE status = 'open';
