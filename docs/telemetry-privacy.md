# Engram telemetry — privacy guarantees

This is the authoritative statement of what Engram's anonymous telemetry does
and does not collect, and how consent is gated. It is enforced in code by the
client payload contract (`src/piia_engram/telemetry.py`), the send-boundary
guard and static contract checks (`src/piia_engram/telemetry_validation.py`),
and the worker allowlists (`worker/src/index.js`), all pinned by the tests under
`tests/test_telemetry*.py`.

## Consent model

- **Telemetry is opt-in.** It is **off by default**. Local stats are written
  only after the owner explicitly enables them (`engram telemetry on`, or
  `ENGRAM_TELEMETRY=1`). With telemetry off, `build_payload()` returns `None` and
  nothing is recorded or sent.
- **Remote sending is a separate opt-in.** Enabling local stats does **not**
  enable network sending. Remote send requires *both* local stats on *and* a
  distinct remote consent (`is_remote_enabled()` is gated on `is_enabled()` and
  defaults to `False`). A failed remote send never raises and never affects MCP
  tool behaviour.
- **Feedback reporting is a third, separate opt-in.** The richer weekly feedback
  report is gated on its own consent (`is_feedback_enabled()`, also gated on
  `is_enabled()`, default `False`).
- **Remote activation stays user-gated.** Standing up / migrating the remote D1
  and deploying the worker are explicit owner actions (see
  [`runbooks/telemetry-contract-remote-closeout.md`](runbooks/telemetry-contract-remote-closeout.md)).
  No assistant pass performs them.
- **Dashboard access is operator-controlled.** The worker's `/` dashboard and
  `/v1/stats` JSON API are gated by the `DASH_PASSWORD` secret. Auth **fails
  open**: with `DASH_PASSWORD` unset, both surfaces are public. Only anonymous,
  metadata-only aggregates (buckets/counts, no PII) are ever exposed there, so a
  public dashboard is a deliberate operator choice rather than a content leak.
  The closeout runbook documents setting the secret if a gated dashboard is
  wanted.
- **Worker dashboard operations are a separate lane.** A Cloudflare Worker UI
  change or health check is not a Python package release, PyPI upload, MCP
  Registry publish, or consent change. Deploying or migrating the Worker remains
  an explicit owner action.

## What is NEVER collected

The telemetry boundary is **metadata-only**. None of the following ever leaves
the machine through telemetry or feedback:

- **No lesson / decision / playbook content** — not the body, title, summary,
  question, choice, reasoning, or any free text. The payload/schema/worker
  allowlists are statically scanned for content-shaped field names and the send
  boundary rejects content-shaped *values*.
- **No file paths** — values containing path separators or a drive prefix are
  rejected at the send boundary.
- **No email or account identity** — values matching an email (including
  homoglyph `@`) or a URL are rejected; no account, login, or device identifier
  is collected.
- **No stable cross-day user ID** — there is no persistent identifier that links
  a person across days. The on-disk `local_uuid` **never leaves the machine**; it
  is only used locally to derive the daily id below.
- **No IP address in the payload**, no IP-derived identifier, device
  fingerprint, prompts, or argument contents of tool calls. (As with any HTTP
  endpoint the Cloudflare edge observes the request's source IP in transit; it is
  not part of the telemetry payload and no IP-based identifier is collected or
  stored.)

## What IS collected (only when opted in), and why it's anonymous

Coarse, bucketed metadata: Engram version, OS family (`win32`/`darwin`/`linux`),
Python `major.minor`, tool tier, per-tool success/error **counts** (no
arguments), knowledge **counts** (no content), closed-vocabulary error
categories, and the v1.1 derived **buckets** (version adoption, activation
state, returning bucket, error trend — all short fixed-vocabulary strings).

- **The daily ID is rotating / bucketed.** It is `HMAC(local_uuid, UTC-date)`
  truncated to 16 hex chars. Because the date is mixed in, the value **changes
  every UTC day** and **cannot be linked across days** — it is a per-day bucket,
  not a person. Counts of distinct `daily_id` therefore approximate
  active-installs-per-day, **not** deduplicated humans.
- **Dashboard wording reflects this.** The worker dashboard counts "匿名日 ID"
  (anonymous daily IDs) and states the rotation caveat; it must not claim
  "独立用户" / "用户数" (unique/distinct users). The `returning_bucket`
  new-vs-returning split is per rotating daily id (approximate churn), not
  deduplicated people. This wording is enforced by
  `validate_dashboard_wording()` and the worker-contract tests.

## How the guarantees are enforced (defense in depth)

1. **Payload contract** — `build_payload()` emits only declared fields and runs
   `_validate_payload()` (rejects oversized/natural-language values and
   path-like keys).
2. **Send-boundary guard** — `validate_feedback_report()` runs an allowlist +
   recursive content check *before* any network serialization, independent of
   what a report builder produced (the worker stores the raw payload, so this
   client gate is the real boundary).
3. **Static contract checks** — `validate_telemetry_contract()` and
   `validate_remote_readiness()` confirm payload ↔ schema ↔ migration ↔ worker
   allowlists stay aligned, migrations are additive/forward-only and correctly
   sequenced, and no content-shaped field exists on any surface.
4. **Opt-out-default check** — `validate_optin_defaults()` statically asserts the
   client keeps telemetry/remote/feedback off by default.

Closeout (applying migrations + deploying the worker) stores anonymous buckets
only and changes **none** of the invariants above.
