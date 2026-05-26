-- Engram anonymous telemetry — D1 schema
-- Each row = one session flush (at most once per user per day)

CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  received   TEXT    NOT NULL DEFAULT (datetime('now')),
  daily_id   TEXT    NOT NULL,               -- HMAC-derived, rotates daily
  version    TEXT    NOT NULL DEFAULT '',     -- engram version e.g. "3.23.0"
  tool_calls TEXT    NOT NULL DEFAULT '{}',   -- JSON {tool: {success:N, error:N}}
  knowledge  TEXT    NOT NULL DEFAULT '{}',   -- JSON {lessons:N, decisions:N, playbooks:N}
  os         TEXT    NOT NULL DEFAULT '',     -- win32 / darwin / linux
  py         TEXT    NOT NULL DEFAULT '',     -- "3.12" (major.minor only)
  tier       TEXT    NOT NULL DEFAULT 'core', -- core / all
  schema_v   INTEGER NOT NULL DEFAULT 1
);

-- Fast lookups
CREATE INDEX IF NOT EXISTS idx_received ON events(received);
CREATE INDEX IF NOT EXISTS idx_daily_id ON events(daily_id);
CREATE INDEX IF NOT EXISTS idx_version  ON events(version);

-- Feedback reports — richer governance/usage snapshots (at most weekly per user)
CREATE TABLE IF NOT EXISTS feedback (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  received        TEXT    NOT NULL DEFAULT (datetime('now')),
  daily_id        TEXT    NOT NULL,               -- same HMAC-derived ID as events
  version         TEXT    NOT NULL DEFAULT '',
  os              TEXT    NOT NULL DEFAULT '',
  py              TEXT    NOT NULL DEFAULT '',
  knowledge_total INTEGER NOT NULL DEFAULT 0,
  staging_count   INTEGER NOT NULL DEFAULT 0,
  verified_count  INTEGER NOT NULL DEFAULT 0,
  promotion_rate  REAL,                           -- 0.0 ~ 1.0
  avg_staging_age REAL,                           -- days
  session_count   INTEGER NOT NULL DEFAULT 0,
  days_active     INTEGER NOT NULL DEFAULT 0,
  source_tools    TEXT    NOT NULL DEFAULT '{}',   -- JSON {tool: count}
  top_domains     TEXT    NOT NULL DEFAULT '{}',   -- JSON {domain: count}
  top_mcp_tools   TEXT    NOT NULL DEFAULT '{}',   -- JSON {tool: count}
  beta_events     TEXT    NOT NULL DEFAULT '{}',   -- JSON aggregate
  raw_json        TEXT    NOT NULL DEFAULT '{}'    -- full report for future analysis
);

CREATE INDEX IF NOT EXISTS idx_feedback_received ON feedback(received);
CREATE INDEX IF NOT EXISTS idx_feedback_daily_id ON feedback(daily_id);
