# Runbook: Telemetry Analysis Contract v1 — remote closeout

Status: **runbook only. DO NOT run the remote steps in this pass.** This is the
checklist Codex/the user follows to finish the contract remotely. The contract
code already landed locally (`60d333c feat: add telemetry analysis contract v1`);
what remains is the D1 migration + worker deploy + verification, which are
explicit, user-gated, public/remote actions.

## 0. What v1 added (recap)

- Client (`src/piia_engram/telemetry.py`): four metadata-only P0 fields in the
  payload — `prev_version`, `session_type`, `install_age_bucket`,
  `error_categories`. Still opt-in; remote send still a separate opt-in.
- Worker (`worker/src/index.js`): `handleEvent` inserts the four fields, with a
  **graceful fallback** to the legacy INSERT if the columns don't exist yet
  (lines ~251-270). Dashboard copy corrected to "匿名日 ID" / "daily_id 按 UTC
  日期轮换" (no "独立用户" / "用户数" claims).
- Schema (`worker/schema.sql`) + migration
  (`worker/migrations/20260603_telemetry_contract_v1.sql`): four columns +
  indexes.

**Key safety property:** because the worker falls back when columns are missing,
**deploy order is flexible** — applying the migration before or after the deploy
will not drop events. Recommended order below minimizes the window where new
fields are silently dropped.

## 1. Pre-flight (local, safe to do now)

```powershell
$py = "E:\Temp\engram-v337-pypi-smoke\Scripts\python.exe"
$env:PYTHONPATH = (Resolve-Path ".\src").Path
& $py -m pytest tests/test_telemetry.py tests/test_telemetry_worker_contract.py -q -p no:cacheprovider
```

Expected: all pass (these are the local guards for the contract). Confirm:

- `worker/wrangler.toml` → `database_id = e06d9bea-...` is the intended prod DB.
- You are authenticated to the correct Cloudflare account (`wrangler whoami`).
- Take note of the current row count for rollback verification (step 5).

## 2. Apply the D1 migration (REMOTE — user-gated)

From `worker/`:

```bash
# Inspect first
wrangler d1 migrations list engram-telemetry --remote

# Dry-run / review the SQL one more time
cat migrations/20260603_telemetry_contract_v1.sql

# Apply
wrangler d1 migrations apply engram-telemetry --remote
```

If the project does not use wrangler's migrations tracking, apply the file
directly (idempotent: it uses `ADD COLUMN` + `CREATE INDEX IF NOT EXISTS`; note
`ADD COLUMN` is NOT idempotent on re-run — only run once):

```bash
wrangler d1 execute engram-telemetry --remote --file=migrations/20260603_telemetry_contract_v1.sql
```

Verify columns exist:

```bash
wrangler d1 execute engram-telemetry --remote --command="PRAGMA table_info(events);"
# expect prev_version, session_type, install_age_bucket, error_categories present
```

## 3. Deploy the worker (REMOTE — user-gated)

From `worker/`:

```bash
wrangler deploy
```

Verify health:

```bash
curl -s https://engram-telemetry.pp3x325.workers.dev/v1/health
# expect a 200 / ok response
```

## 4. Dashboard smoke test

```bash
# 4.1 POST a synthetic event with the new fields (anonymous endpoint)
curl -s -X POST https://engram-telemetry.pp3x325.workers.dev/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"schema":1,"daily_id":"smoke_test_000","engram_version":"3.45.3",
       "prev_version":"3.45.2","session_type":"regular",
       "install_age_bucket":"31_plus_days",
       "error_categories":{"timeout":1},
       "os_platform":"linux","python_version":"3.12","tools_tier":"core"}'
# expect {"ok":true} with HTTP 201

# 4.2 Confirm it stored WITH the new columns populated (not the fallback path)
wrangler d1 execute engram-telemetry --remote \
  --command="SELECT daily_id, prev_version, session_type, install_age_bucket, error_categories FROM events WHERE daily_id='smoke_test_000';"
# expect prev_version=3.45.2, session_type=regular, install_age_bucket=31_plus_days, error_categories={"timeout":1}

# 4.3 Dashboard copy check (login required for full stats; HTML is public-ish)
curl -s https://engram-telemetry.pp3x325.workers.dev/ | grep -E "匿名日 ID|独立用户" || true
# expect to SEE "匿名日 ID" and NOT see "独立用户"
```

Then clean up the synthetic row:

```bash
wrangler d1 execute engram-telemetry --remote \
  --command="DELETE FROM events WHERE daily_id='smoke_test_000';"
```

## 5. Rollback / verification

- **Worker rollback:** `wrangler rollback` (or redeploy the previous version) —
  the previous worker still works against the migrated DB because new columns
  have defaults.
- **Migration rollback:** SQLite/D1 `ADD COLUMN` cannot be cleanly dropped on
  older SQLite; treat the migration as forward-only. The columns are additive
  with safe defaults (`''` / `'{}'`), so leaving them in place after a worker
  rollback is harmless — the old worker simply ignores them.
- **Verify no data loss:** compare `SELECT COUNT(*) FROM events;` against the
  count noted in step 1 (plus any legitimately received events).

## 6. Optional local worker tests (if useful later)

`tests/test_telemetry_worker_contract.py` is a static contract check (asserts the
worker source + schema reference the four fields and use the corrected dashboard
wording). A heavier option, not added here, would be a Node-side test using
`wrangler dev --local` + an in-memory D1 to exercise `handleEvent`'s real INSERT
and the fallback branch. Track as a follow-up only if the worker grows.

## 7. Gates / do-not

- This whole runbook is remote/public. Per the D+ rules, **none of §2-§4 runs
  without explicit user confirmation.**
- Do not change `database_id`, telemetry endpoints, or the opt-in defaults as
  part of closeout.
- Telemetry stays opt-in; remote send stays a separate opt-in. Closeout does not
  enable anything for users — it only makes the server able to store the fields
  that opted-in clients already send.
