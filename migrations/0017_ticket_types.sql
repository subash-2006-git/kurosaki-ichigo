-- Ticket types (one button each, with their own staff and category), idle auto-close, and ratings.
CREATE TABLE tickets.types (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    guild_id        BIGINT NOT NULL,
    label           VARCHAR(80) NOT NULL,
    emoji           TEXT,
    staff_role_ids  BIGINT[] NOT NULL DEFAULT '{}',  -- added to the server-wide staff roles
    category_id     BIGINT                           -- NULL = the server-wide ticket category
);
CREATE INDEX tickets_types_guild_idx ON tickets.types (guild_id);

ALTER TABLE tickets.tickets
    ADD COLUMN type_id          BIGINT REFERENCES tickets.types (id) ON DELETE SET NULL,
    ADD COLUMN rating           SMALLINT CHECK (rating BETWEEN 1 AND 5),
    ADD COLUMN idle_warning_id  BIGINT;  -- the "closing soon" message, while it's still the last one

ALTER TABLE tickets.settings
    ADD COLUMN idle_close_hours  INTEGER NOT NULL DEFAULT 0 CHECK (idle_close_hours BETWEEN 0 AND 720),
    ADD COLUMN idle_warn_hours   INTEGER NOT NULL DEFAULT 12 CHECK (idle_warn_hours BETWEEN 1 AND 719),
    ADD COLUMN ask_rating        BOOLEAN NOT NULL DEFAULT TRUE;
