# Cursor stop-hook governed writeback — design (no silent memory)

Status: **design / runbook only.** No writeback implementation in this pass. The
deliberate default is: **Engram does not silently write memory from a Cursor stop
event.** Anything here ships only after explicit opt-in and the governance gates
below are in place.

## 1. Principle

Passive, end-of-session memory capture is the single highest-risk path in a
memory product: it can turn unreviewed conversation into "trusted" knowledge
without the user ever seeing it. So this design starts from a hard NO and only
relaxes under strict, auditable conditions.

The non-negotiable invariants (any implementation must satisfy ALL):

```text
I1  Opt-in           — off unless the user explicitly enables it.
I2  Staging-default  — every captured item lands in staging, never verified.
I3  Content hash     — each item carries a hash for dedup + tamper-evidence.
I4  Audit log        — every writeback appends a metadata-only audit record.
I5  User-visible     — captured items are listable/reviewable by the user.
I6  No auto-promote  — nothing becomes verified without an explicit user action.
```

## 2. What exists today (reference)

From `src/piia_engram/hooks/`:

- `auto_save_on_stop.py` — Claude Code Stop / PreCompact hook; saves session
  *context* and (above a turn threshold) calls `wrap_up_session`.
- `auto_inject_resume_brief.py` — SessionStart; **read-only** injection.
- `auto_absorb_compact.py` — PostCompact; appends to the **daily log only**
  (a v3.31 decision narrowed it to daily-log to stop double-writing staging).
- Re-entry guard via `CLAUDE_INVOKED_BY=engram_*`.

Cursor has no equivalent governed stop hook today. The existing hooks already
lean toward "log / staging, not verified," but the tier is not *explicitly*
enforced at the hook boundary. This design makes that explicit for Cursor.

## 3. Design

### 3.1 Trigger & opt-in

- A Cursor stop/end-of-conversation event invokes a dedicated hook module
  (e.g. `hooks/cursor_writeback.py`).
- The hook **no-ops unless** an explicit opt-in is set (a config flag, e.g.
  `ENGRAM_CURSOR_WRITEBACK=1` plus a persisted consent timestamp). Default off.
- Re-entry guard mirrors the existing `CLAUDE_INVOKED_BY` pattern to avoid loops.

### 3.2 What gets written (and where)

```text
Always:   a session context checkpoint (like save_agent_context) — operational,
          not "knowledge", and clearly labeled as a session artifact.

Optional: extracted candidate lessons/decisions — ONLY to the staging tier,
          tagged source=cursor_writeback, with:
            - content_hash (sha256 of normalized content)
            - run_id / session id (provenance — see Provenance Contract v1)
            - source_agent = "cursor"
            - tier = "staging", approval_status = "pending"
```

Never: direct writes to verified/trusted tier. Never: writes to another tool's
config.

Sensitivity handling before anything is written (the existing classifier screens
content first):

```text
secret-class   -> DROPPED, never written (credential shapes, etc.)
private-class  -> DROPPED by default (PII: email/phone/id). May only be staged
                  if the user has explicitly opted into private-content capture;
                  default off. Staged private items remain owner-only + reviewable.
work/public    -> eligible for staging (still pending, never auto-promoted)
```

This makes the `private` (PII) case explicit: it is not silently staged on the
default path — only `work`/`public` candidates reach staging unless the user
opts in to private capture.

### 3.3 Content hash & dedup

- `content_hash = sha256(normalized_summary + "\n" + normalized_detail)`.
- Before staging, skip if a staging/verified item with the same hash exists
  (idempotent re-runs; protects against a flapping stop event double-writing).
- The hash is also the tamper-evidence anchor in the audit record.

### 3.4 Audit log

Each writeback appends one metadata-only record to the audit ledger:

```json
{
  "ts": "...", "action": "writeback", "source": "cursor_writeback",
  "session_id": "...", "run_id": "...",
  "staged_count": 3, "skipped_duplicate": 1, "dropped_sensitive": 0,
  "content_hashes": ["...","...","..."]
}
```

No conversation content, no file paths. This reuses the existing append-only
audit/governance ledger machinery.

### 3.5 User-visible review

- Staged items appear in the existing review surfaces (`engram review`, the HTML
  review page) under a `cursor_writeback` filter.
- Promotion to verified uses the existing `promote_knowledge` / `apply_review`
  path — i.e. an explicit user action. The hook never promotes.

## 4. Governance integration

- Writeback runs through `maybe_refuse_write` semantics: when governance is on,
  a non-owner caller cannot force verified writes; the hook only ever requests a
  staging write, which is the least-privileged write.
- The capture is subject to the sensitivity classifier *before* staging, so
  `secret`-class content never reaches disk via this path.

## 5. Runbook: enabling safely (when implemented)

```text
1. Read this design and confirm I1–I6 are all satisfied by the build.
2. Set the opt-in flag; confirm a consent timestamp is persisted.
3. Run one Cursor session; stop it.
4. Verify: items are in STAGING only (engram review shows them as pending).
5. Verify: audit ledger has one writeback record, metadata-only.
6. Re-run the same session; verify dedup (no duplicates by content_hash).
7. Put a fake secret in the conversation; verify it is dropped, not staged.
8. Confirm nothing was auto-promoted to verified.
9. Only then consider the feature "validated" for that environment.
```

## 6. Rollout phases

1. ✅ **Done** — Spec (this doc) + the **pure** governed-writeback preparation
   helper and invariant tests that need no live Cursor:
   `continuity_harness.prepare_writeback_candidates` (content-hash/dedup,
   sensitivity-drop of secret + private-by-default, staging-tier/pending tagging,
   metadata-only audit record with `applied: false`) and the simulated E2E
   cycle `simulate_continuity_cycle`. Tested in `tests/test_continuity_harness.py`
   across Codex/Claude/Cursor-style export inputs, asserting no
   staging/sensitive/just-staged content leaks into exported continuity material.
   **No live hook ships and nothing is written to disk.**
2. **Deferred (gated)** — Hook module behind the opt-in flag, default no-op.
3. **Deferred (gated)** — Review-surface filter + audit record persistence.
4. **Deferred (gated)** — Live Cursor validation per §5 before it is documented
   as available.

## 7. Non-goals

- No verified-tier writeback from a hook, ever.
- No cross-tool config writes.
- No "smart" auto-promotion heuristics — promotion stays a human decision.
