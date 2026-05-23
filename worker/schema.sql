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
