-- Birthdays: members save a day and month (no year); the bot announces them and gives a role for the day.
CREATE SCHEMA IF NOT EXISTS birthdays;

CREATE TABLE birthdays.settings (
    guild_id    BIGINT PRIMARY KEY,
    enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    channel_id  BIGINT,
    role_id     BIGINT,                          -- given for the day, optional
    message     TEXT,                            -- NULL = the built-in message
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    hour        SMALLINT NOT NULL DEFAULT 0 CHECK (hour BETWEEN 0 AND 23),
    last_run    DATE                             -- the server's local date birthdays were last handled
);

CREATE TABLE birthdays.birthdays (
    guild_id  BIGINT NOT NULL,
    user_id   BIGINT NOT NULL,
    month     SMALLINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day       SMALLINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    PRIMARY KEY (guild_id, user_id)
);
CREATE INDEX birthdays_date_idx ON birthdays.birthdays (guild_id, month, day);
CREATE INDEX birthdays_user_idx ON birthdays.birthdays (user_id);
