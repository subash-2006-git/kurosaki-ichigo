-- One Postgres schema per feature so generic table names (guild_settings, jobs, cases, ...) never collide.
-- core.schema_migrations itself is created by core/migrate.py before any file runs.
CREATE SCHEMA IF NOT EXISTS community;
CREATE SCHEMA IF NOT EXISTS moderation;
CREATE SCHEMA IF NOT EXISTS nickname;
