-- Daily usage counts — one row per daily rotating id per UTC day
-- (closed-set labels and count buckets only; no install id, no IP, no receive time)
CREATE TABLE IF NOT EXISTS counts (
  daily_id    TEXT NOT NULL,
  day         TEXT NOT NULL,
  version     TEXT NOT NULL,
  os          TEXT NOT NULL,
  python      TEXT NOT NULL,
  tier        TEXT NOT NULL,
  install_age TEXT NOT NULL,
  adoption    TEXT NOT NULL,
  tools       TEXT NOT NULL,
  errors      TEXT NOT NULL,
  knowledge   TEXT NOT NULL,
  PRIMARY KEY (daily_id, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_counts_day ON counts(day);
