# Runbook: Telemetry Analysis Contract — remote closeout (consolidated v1 + v1.1)

Status: **runbook only. DO NOT run the remote steps in this pass.** This is the
single canonical sequence for finishing the telemetry contract remotely. It
supersedes the split instructions in:

- [`telemetry-contract-v1-remote-closeout.md`](telemetry-contract-v1-remote-closeout.md) (P0 detail / recap)
- [`telemetry-contract-v1_1-remote-closeout.md`](telemetry-contract-v1_1-remote-closeout.md) (P1 detail / recap)

Use **this** file as the running checklist; consult the two above only for the
"what each contract added" background. Everything below is explicit, user-gated,
public/remote action. Nothing here runs without the owner's confirmation.

## Placeholders (fill in privately — do not commit real values)

| Placeholder | Meaning |
| --- | --- |
| `<telemetry-worker-host>` | The deployed worker hostname. |
| `<DB>` | The D1 database name/binding the worker uses. |

These are kept as placeholders on purpose. (The client's default endpoint host
is already present in committed source, so this is hygiene rather than secrecy:
a runbook full of copy-pasteable `--remote` commands against a named database is
exactly the thing not to hand someone verbatim.) The owner substitutes the real
values locally at run time.

## Dashboard password rotation

The remote dashboard secret is `DASH_PASSWORD`. Cloudflare Worker secrets cannot
be read back in plaintext after they are written, so rotation must be
owner-handoff first: generate or provide the password, show it to the owner, let
the owner record it in the private credential store, then apply it.

```bash
powershell -File ./scripts/rotate_telemetry_dashboard_password.ps1 -Generate
powershell -File ./scripts/rotate_telemetry_dashboard_password.ps1 -Generate -Apply
```

The script only writes Cloudflare Worker secret state when `-Apply` is present.
It uses `wrangler secret put DASH_PASSWORD`; changing the secret invalidates
existing dashboard sessions because the session cookie is derived from
`DASH_PASSWORD`.

## What stays user-gated (never run from an assistant pass)

- `wrangler d1 execute ... --remote` (remote D1 migration).
- `wrangler deploy` (Cloudflare worker deploy).
- Any POST/DELETE against the live `/v1/events` endpoint (smoke insert / cleanup).
- Any change to the remote endpoint, DB binding, or the opt-in defaults.

Closeout never enables anything for users. It only lets the server *store* the
fields that already-opted-in clients send. Telemetry stays opt-in; remote send
and feedback stay separate opt-ins.

---

## 1. Local validation (safe — run this first, every time)

```powershell
# Static contract consistency + the pre-deploy readiness checklist. Both are
# read-only and perform NO network/D1/deploy action. Exit 0 = green.
engram telemetry-validate
engram telemetry-validate --remote-readiness

# The same checks run in CI:
python -m pytest tests/test_telemetry_validation.py tests/test_telemetry_worker_smoke.py -q
```

`--remote-readiness` confirms, in one shot: payload↔schema mapping, worker
event + feedback allowlists, both migration files present, **v1-before-v1.1
sequencing**, dashboard anonymous-daily-id wording + v1.1 tiles, client opt-out
defaults, and no content field on any surface. **Do not proceed past this
section unless both commands exit 0.**

Also confirm before touching remote:

- `worker/wrangler.toml` points at the intended production `<DB>`.
- You are authenticated to the correct Cloudflare account (`wrangler whoami`).
- Note the current row count for the rollback check (step 9):
  `wrangler d1 execute <DB> --remote --command="SELECT COUNT(*) FROM events;"`

## 2. Apply the v1 D1 migration (REMOTE — user-gated)

Apply **only if** the DB predates the P0 columns (`prev_version`,
`session_type`, `install_age_bucket`, `error_categories`). Inspect first:

```bash
wrangler d1 execute <DB> --remote --command="PRAGMA table_info(events);"
# If the four P0 columns are already present, SKIP to step 3.

wrangler d1 execute <DB> --remote \
  --file=worker/migrations/20260603_telemetry_contract_v1.sql
```

`ADD COLUMN` is NOT idempotent on re-run — apply exactly once.

## 3. Apply the v1.1 D1 migration (REMOTE — user-gated, AFTER step 2)

```bash
# Run exactly once — ADD COLUMN errors on a second run ("duplicate column").
wrangler d1 execute <DB> --remote \
  --file=worker/migrations/20260603_telemetry_contract_v1_1.sql

wrangler d1 execute <DB> --remote --command="PRAGMA table_info(events);"
# expect: contract_version, version_adoption, activation_state,
#         returning_bucket, error_trend
```

If unsure whether it already ran, inspect `PRAGMA table_info(events)` first — do
not blind re-run.

## 4. Deploy the worker (REMOTE — user-gated)

```bash
cd worker && wrangler deploy
```

**Dashboard access control — set the secret before relying on a gated
dashboard.** The worker's `/` dashboard and `/v1/stats` JSON API are protected
by `DASH_PASSWORD`, but the auth **fails open**: when `DASH_PASSWORD` is *unset*,
`isAuthenticated()` returns `true` and the login form accepts any password, so
both surfaces are fully public. The data exposed that way is anonymous,
metadata-only aggregates (buckets/counts, no PII) by design, so a public
dashboard is a deliberate-but-explicit choice — not a leak of any user's
content. If you want the dashboard gated, set the secret once:

```bash
cd worker && wrangler secret put DASH_PASSWORD
```

If you intend the dashboard to be public, that is fine — just be aware it is
public until/unless the secret is set; the step below assumes the secret exists.

Order safety: the worker uses a tiered INSERT (full v1.1 → v1 → legacy) and
falls back when columns are missing, so deploy-before-migrate or
migrate-before-deploy both avoid dropping events. The order above (migrate, then
deploy) just minimizes the window where new fields land on the fallback path.

## 5. Health check (REMOTE — user-gated)

```bash
curl -s https://<telemetry-worker-host>/v1/health
# expect: {"status":"ok","service":"engram-telemetry"} (HTTP 200)
```

## 6. Insert a smoke event (REMOTE — user-gated)

Anonymous endpoint; the synthetic `daily_id` is obviously fake so it is easy to
delete in step 8.

```bash
curl -s -X POST https://<telemetry-worker-host>/v1/events \
  -H 'Content-Type: application/json' \
  -d '{"schema":1,"daily_id":"smoke_full_000","engram_version":"3.47.0",
       "prev_version":"3.46.0","session_type":"regular",
       "install_age_bucket":"31_plus_days","error_categories":{"timeout":1},
       "contract_version":"1.1","version_adoption":"upgrade",
       "activation_state":"activated","returning_bucket":"returning",
       "error_trend":"up","os_platform":"linux","python_version":"3.12",
       "tools_tier":"core"}'
# expect: {"ok":true} (HTTP 201)
```

## 7. Verify the full-path columns populated (REMOTE — user-gated)

Confirms the event stored on the **full v1.1 path**, not the fallback.

```bash
wrangler d1 execute <DB> --remote --command="
  SELECT prev_version, session_type, install_age_bucket,
         contract_version, version_adoption, activation_state,
         returning_bucket, error_trend
  FROM events WHERE daily_id='smoke_full_000';"
# expect every column populated (e.g. contract_version=1.1,
# version_adoption=upgrade, error_trend=up) — none blank.
```

Dashboard copy check — do this **locally against the source**, not by curling
the live site: when `DASH_PASSWORD` is set (step 4) the dashboard `/` route is
password-gated, so an unauthenticated `curl https://<host>/` only returns the
login page (it contains neither "匿名日 ID" nor "独立用户"), which makes a `grep`
give false confidence. (With `DASH_PASSWORD` unset the route is public and the
curl would return the rendered dashboard — but you should still copy-check
against the source.) The readiness
CLI verifies the wording + v1.1 tiles statically from `worker/src/index.js`:

```powershell
engram telemetry-validate --remote-readiness   # 'dashboard_wording' must be [ok]
```

If you do want to eyeball the rendered live dashboard, open
`https://<telemetry-worker-host>/` in a browser and log in first (an
unauthenticated `curl` will only ever see the login page).

## 8. Delete the smoke row (REMOTE — user-gated)

```bash
wrangler d1 execute <DB> --remote \
  --command="DELETE FROM events WHERE daily_id='smoke_full_000';"
```

## 9. Rollback notes

- **Worker rollback:** `wrangler rollback` (or redeploy the previous build). The
  previous worker still works against the migrated DB because every new column
  has a safe default (`''` / `'{}'`).
- **Migration rollback:** D1/SQLite `ADD COLUMN` is not cleanly droppable on
  older SQLite — treat both migrations as **forward-only**. The columns are
  additive with safe defaults, so leaving them after a worker rollback is
  harmless: the old worker ignores them.
- **Data-loss check:** compare `SELECT COUNT(*) FROM events;` against the count
  noted in step 1 (plus any legitimately received events). The tiered INSERT
  guarantees no opted-in event is dropped regardless of migrate/deploy order.

---

## Appendix: privacy invariants this closeout must not break

See [`../telemetry-privacy.md`](../telemetry-privacy.md) for the full statement.
In short: telemetry is opt-in, feedback is a separate opt-in, no
lesson/decision/playbook content / file paths / email / account identity is
ever sent, the `daily_id` is a rotating HMAC bucket (not a stable cross-day user
id), and remote activation stays user-gated. Closeout stores anonymous buckets
only — it changes none of these invariants.
