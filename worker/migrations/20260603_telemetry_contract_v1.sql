-- Telemetry Analysis Contract v1 (P0 fields)
-- Apply once before relying on dashboard/API analysis for these fields.

ALTER TABLE events ADD COLUMN prev_version TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN session_type TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN install_age_bucket TEXT NOT NULL DEFAULT '';
ALTER TABLE events ADD COLUMN error_categories TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_prev_version ON events(prev_version);
CREATE INDEX IF NOT EXISTS idx_session_type ON events(session_type);
CREATE INDEX IF NOT EXISTS idx_install_age_bucket ON events(install_age_bucket);
