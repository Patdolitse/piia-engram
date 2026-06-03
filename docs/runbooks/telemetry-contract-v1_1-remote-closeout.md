# Runbook: Telemetry Analysis Contract v1.1 — remote closeout

> **Consolidated sequence:** the single canonical step-by-step closeout (local
> validation → v1 migration → v1.1 migration → deploy → health → smoke →
> verify → cleanup → rollback) now lives in
> [`telemetry-contract-remote-closeout.md`](telemetry-contract-remote-closeout.md).
> Follow that as the running checklist. This file is kept for the **P1 detail /
> recap** of what v1.1 added and its sequencing rationale.

Status: **runbook only. DO NOT run the remote steps in this pass.** This extends
`telemetry-contract-v1-remote-closeout.md` with the v1.1 sequencing. The v1.1
contract code already landed locally (client derived buckets in
`src/piia_engram/telemetry.py`, `worker/schema.sql`, and
`worker/migrations/20260603_telemetry_contract_v1_1.sql`). What remains is the
D1 migration apply + worker deploy + verification — all explicit, user-gated,
public/remote actions.

## 0. What v1.1 added (recap)

Five anonymous **derived buckets**, computed client-side from existing metadata
(no new raw data): `contract_version`, `version_adoption`, `activation_state`,
`returning_bucket`, `error_trend`. Transport `schema` stays `1`;
`contract_version` flags the analytic level so a reader can separate v1 from
v1.1 events. Still opt-in; remote send is still a separate opt-in.

## 1. Local validation BEFORE any remote step (run these)

```bash
# Static contract consistency: payload <-> schema <-> migration, additive-only,
# and no content field on either side. Exit 0 = consistent.
engram telemetry-validate
#   or: python -c "from piia_engram.telemetry_validation import *; \
#       import json,sys; r=validate_telemetry_contract('worker'); \
#       print(render_validation_text(r)); sys.exit(0 if r['ok'] else 1)"

# The same checks run in CI via tests/test_telemetry_validation.py.
python -m pytest tests/test_telemetry_validation.py -q
```

Do not proceed to §2 unless `telemetry-validate` is OK.

## 2. Migration & deploy sequencing (USER-GATED — do not run here)

The worker degrades gracefully (tiered INSERT fallback) whether or not the v1.1
columns exist, so **no event is dropped regardless of order**. The safe order is
still:

```text
1. Apply v1 migration first if the DB predates it:
   wrangler d1 execute <DB> --file worker/migrations/20260603_telemetry_contract_v1.sql --remote
2. Apply v1.1 migration EXACTLY ONCE (ADD COLUMN is not idempotent):
   wrangler d1 execute <DB> --file worker/migrations/20260603_telemetry_contract_v1_1.sql --remote
3. Deploy the worker:
   wrangler deploy   (from worker/)
4. Verify: send one opt-in event from a test install and confirm the v1.1
   columns populate; confirm legacy clients (no v1.1 fields) still insert via
   the fallback path.
```

Idempotency note: re-running step 2 will error (`duplicate column`). That is
expected — run it once. If unsure whether it was applied, inspect the table
columns first (`PRAGMA table_info(events)`), do not blind-re-run.

## 3. Rollback posture

The migration is additive and forward-only (asserted by
`tests/test_telemetry_validation.py::test_v1_1_migration_is_additive`). There is
no destructive rollback to author: leaving the v1.1 columns in place is harmless
to v1 readers. If a deploy must be reverted, redeploy the previous worker build;
the columns can stay.

## 4. What stays user-gated

- `wrangler d1 execute ... --remote` (remote D1 migration).
- `wrangler deploy` (Cloudflare worker deploy).
- Any change to the remote endpoint or DB binding.

All of the above are public/remote actions performed only by the user, after the
local validation in §1 is green.
