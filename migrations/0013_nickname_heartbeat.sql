-- When a running nickname job last saved progress. On startup, only jobs that have gone quiet are
-- marked interrupted, so a deploy's new instance doesn't mislabel the old instance's live job.
ALTER TABLE nickname.jobs ADD COLUMN heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
