-- Telemetry Analysis Contract v1.1 (P1 derived buckets)
-- Apply AFTER 20260603_telemetry_contract_v1.sql. Forward-only and additive:
-- the worker degrades gracefully (tiered INSERT fallback) whether or not this
-- has been applied, so deploy/migration order is flexible — no event is dropped.
--
-- NOTE: ADD COLUMN is NOT idempotent on re-run; run this migration exactly once.

ALTER TABLE events ADD COLUMN contract_version TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN version_adoption TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN activation_state TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN returning_bucket TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN error_trend TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_version_adoption ON events(version_adoption);
CREATE INDEX IF NOT EXISTS idx_returning_bucket ON events(returning_bucket);
