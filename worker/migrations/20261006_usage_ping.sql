-- Daily usage ping — one row per install per UTC day (random install id; no IP stored)
CREATE TABLE IF NOT EXISTS pings (
  install_id TEXT NOT NULL,
  day        TEXT NOT NULL,
  version    TEXT NOT NULL,
  os         TEXT NOT NULL,
  python     TEXT NOT NULL,
  client     TEXT NOT NULL,
  PRIMARY KEY (install_id, day)
);
CREATE INDEX IF NOT EXISTS idx_pings_day_install ON pings(day, install_id);

-- Dashboard sessions (hash of a random token, bound to the current password)
CREATE TABLE IF NOT EXISTS dash_sessions (
  token_hash TEXT PRIMARY KEY,
  pw_tag     TEXT NOT NULL,
  expires    INTEGER NOT NULL
);

-- Failed dashboard logins (timestamps only) for the lockout window
CREATE TABLE IF NOT EXISTS login_failures (
  ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_failures_ts ON login_failures(ts);
