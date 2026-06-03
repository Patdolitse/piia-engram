# Runbook: Telemetry Analysis Contract v1 — remote closeout

> **Consolidated sequence:** the single canonical step-by-step closeout (local
> validation → v1 migration → v1.1 migration → deploy → health → smoke →
> verify → cleanup → rollback) now lives in
> [`telemetry-contract-remote-closeout.md`](telemetry-contract-remote-closeout.md).
> Follow that as the running checklist. This file is kept for the **P0 detail /
> recap** of what v1 added.

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
$py = "<path-to-your-python.exe>"
$env:PYTHONPATH = (Resolve-Path ".\src").Path
& $py -m pytest tests/test_telemetry.py tests/test_telemetry_worker_contract.py -q -p no:cacheprovider
```

Expected: all pass (these are the local guards for the contract). Confirm:

- `worker/wrangler.toml` points at the intended production D1 database.
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
directly (only partially idempotent: the `CREATE INDEX` is `IF NOT EXISTS`, but
`ADD COLUMN` is NOT idempotent on re-run — apply exactly once):

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
curl -s https://<telemetry-worker-host>/v1/health
# expect a 200 / ok response
```

## 4. Dashboard smoke test

```bash
# 4.1 POST a synthetic event with the new fields (anonymous endpoint)
curl -s -X POST https://<telemetry-worker-host>/v1/events \
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
curl -s https://<telemetry-worker-host>/ | grep -E "匿名日 ID|独立用户" || true
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

- This whole runbook is remote/public. Per the project's remote-action gating
  policy, **none of §2-§4 runs without explicit user confirmation.**
- Do not change `database_id`, telemetry endpoints, or the opt-in defaults as
  part of closeout.
- Telemetry stays opt-in; remote send stays a separate opt-in. Closeout does not
  enable anything for users — it only makes the server able to store the fields
  that opted-in clients already send.

## 8. Telemetry Analysis Contract v1.1 (P1) — additional closeout

Status: **runbook only — DO NOT run the remote steps in this pass.** v1.1 is
strictly additive over v1. The client (`telemetry.py`) now also emits five
derived, anonymous **bucket** fields, and the worker/schema drafts know how to
store them. Nothing is deployed here.

### 8.0 What v1.1 added (local, landed)

- Client: `contract_version` (`"1.1"`), `version_adoption`
  (`first/same/upgrade/downgrade/changed`), `activation_state`
  (`activated/not_activated/unknown`), `returning_bucket` (`new/returning`,
  per the rotating daily id — **not** a person), and `error_trend`
  (`none/first/up/down/flat`). `schema` stays `1` (transport unchanged); the new
  `contract_version` field is how a reader tells v1 from v1.1 events apart.
  Guarded by `tests/test_telemetry_contract_v1_1.py`.
- Worker (`worker/src/index.js`): a **tiered INSERT** — full v1.1 → v1 (P0) →
  legacy. So the migration may land before or after the deploy without dropping
  events (same safety property as v1, now across both column sets).
- Schema (`worker/schema.sql`) + migration
  (`worker/migrations/20260603_telemetry_contract_v1_1.sql`): five `TEXT`
  columns + two indexes.

### 8.1 Apply the v1.1 migration (REMOTE — user-gated, run AFTER the v1 one)

```bash
# from worker/ — run exactly once (ADD COLUMN is not idempotent on re-run)
wrangler d1 execute engram-telemetry --remote \
  --file=migrations/20260603_telemetry_contract_v1_1.sql

wrangler d1 execute engram-telemetry --remote --command="PRAGMA table_info(events);"
# expect contract_version, version_adoption, activation_state, returning_bucket, error_trend
```

Then `wrangler deploy` (same as §3) and extend the §4 smoke event with the new
fields, verifying they store on the full path (not the fallback):

```bash
curl -s -X POST https://<telemetry-worker-host>/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"schema":1,"daily_id":"smoke_v11_000","engram_version":"3.45.3",
       "prev_version":"3.45.2","session_type":"regular",
       "install_age_bucket":"31_plus_days","error_categories":{"timeout":1},
       "contract_version":"1.1","version_adoption":"upgrade",
       "activation_state":"activated","returning_bucket":"returning",
       "error_trend":"up","os_platform":"linux","python_version":"3.12",
       "tools_tier":"core"}'

wrangler d1 execute engram-telemetry --remote \
  --command="SELECT contract_version, version_adoption, activation_state, returning_bucket, error_trend FROM events WHERE daily_id='smoke_v11_000';"
# then DELETE the smoke row as in §4.
```

### 8.2 Dashboard / analytics copy

The dashboard already counts **匿名日 ID**, not "users"/"独立用户" (locked by
`test_dashboard_copy_uses_daily_id_wording_instead_of_user_count_claims`). The
v1.1 adoption/activation/returning metrics are likewise anonymous-daily-id based
— any new dashboard tiles MUST keep that wording and must not imply unique
humans. The `returning_bucket` "new/returning" split is per rotating daily id,
so it approximates churn, not deduplicated people.

### 8.3 Gates / do-not (v1.1)

- Same as §7: §8.1 is remote/public and runs only with explicit user
  confirmation. v1.1 adds **no** content fields and does not change opt-in
  defaults — it only adds anonymous buckets the opted-in client already sends.
