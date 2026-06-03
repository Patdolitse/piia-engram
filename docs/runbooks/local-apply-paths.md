# Runbook: Local Owner-Confirmed Apply Paths (N2 / N4 / N5 / D)

Status: **implemented (CLI / owner-only, metadata-only) + tests.**

This runbook documents the local product batch that adds three owner-confirmed
"apply" paths and two read-only surfacings on top of the existing proposal
layers. Everything here is **CLI / owner-only and metadata-only**. None of it
adds an agent-facing MCP apply tool, changes telemetry, changes
permission/governance, hard-deletes, or publishes anything.

## 0. The shared safety contract

Every apply path below obeys the same gate (mirrors `engram lifecycle apply`):

> **dry-run is the default -> apply requires `--commit --yes` -> fail closed.**

- `engram <cmd> apply` with no flags is a **dry-run preview** - it mutates
  nothing and returns the plan.
- `--commit` without `--yes` **fails closed**: it reports
  `requires_confirmation`, mutates nothing, and exits non-zero so scripts notice.
- `--commit --yes` is the only path that mutates, and only via existing reviewed
  write primitives.
- Every payload and audit line is **metadata only**: ids, types, scores,
  outcomes - never stored bodies, summaries, or private paths.

## 1. Near-duplicate merge apply (N4)

Module: `src/piia_engram/merge_apply.py`. CLI: `engram merge`.

- `engram merge [--threshold T] [--limit N] [--json]` - read-only
  near-duplicate preview using the same metadata-only dry-run payload as
  `engram merge apply`. It does not echo suggestion summaries or stored bodies.
- `engram merge apply [--pair PRIMARY:SECONDARY ...] [--commit] [--yes]` -
  folds each secondary into its primary via the existing **reversible soft
  archive** `Engram.merge_knowledge`. The secondary is marked
  `status="outdated"` + `merged_into=<primary>` and its relations transfer to
  the primary. **Never a hard delete** - the row still exists and the merge is
  auditable.
- Pairs default to `suggest_merges` output when `--pair` is omitted.
- A pair whose ids are missing/identical, or whose secondary is no longer active
  (e.g. already merged), is a reported **skip**, never a crash.

Tests: `tests/test_merge_apply.py` (dry-run/ fail-closed/ confirmed/ idempotent/
metadata-only/ CLI).

## 2. Reconcile import apply (N2) - import-only

Module: `src/piia_engram/reconcile_apply.py`. CLI: `engram reconcile`.

- `engram reconcile` - scans external AI memory files
  (`Engram.collect_memory_candidates`, read-only) and classifies each candidate
  via `reconcile_proposal.build_reconcile_proposal` as
  `import` / `duplicate` / `conflict` / `skip`. Imports nothing.
- `engram reconcile apply [--commit] [--yes]` - imports **only** the novel
  (`import`) candidates via the existing `add_lesson` / `add_decision` write
  API (`tier=staging`).
- **Duplicates and conflicts are never applied.** They are surfaced as
  metadata-only no-ops and **never mutate an existing lesson or decision**.
  Conflict->supersede resolution is **deferred** to a later, separately-reviewed
  slice (a conflict today only reports "same question, different choice").
- `engram reconcile conflicts [--json]` is the conflict-preview v2 surface. It
  returns only candidate indexes, action/reason/type, scores, and match ids. It
  never imports, supersedes, overwrites, or echoes question/choice/reasoning
  bodies.
- No public / agent mutation surface is added.

Tests: `tests/test_reconcile_apply.py` (dry-run/ fail-closed/ import-only/
duplicate no-op/ conflict-does-not-mutate-existing/ metadata-only/ CLI).

## 3. Version-chain HEAD surfacing (N5) - render-only

Helper: `version_chain.head_ids(edges)` returns the set of current HEAD ids
across all chains. The surfacings are **additive and render-only** - they never
change what is stored or selected:

- **Recall** (`recall_service.gather_recall`): `meta.version_chain =
  {collapsed, heads_present}` and the text footer reports
  "current versions/HEAD surfaced: N". Superseded older versions stay collapsed
  behind their HEAD (existing behavior); their bodies never surface.
- **Resume brief** (`contexts.get_resume_brief`): when the store holds any
  superseded version chains, the handoff adds one guarded line noting how many
  chains/superseded versions exist and that recall/dashboard surface the current
  HEAD. Guarded by `try/except`; absent when there are no chains.

Tests: `tests/test_version_chain_surfacing.py`.

## 4. Owner dashboard readiness counts (D) - metadata-only

`owner_dashboard.build_owner_dashboard` now accepts optional already-computed
`merge_report` / `reconcile_report` / `version_report` and emits a
`readiness` block of **counts only**:

```
readiness:
  lifecycle:     {archive_candidates, prune_candidates, pending_apply}
  reconcile:     {import, duplicate, conflict}
  merge:         {candidates}
  version_chain: {topics, heads, superseded}
```

`engram dashboard` computes those three reports read-only (`suggest_merges`,
`build_reconcile_proposal` over scanned candidates, `build_version_report` over
the relation edges) and passes them in. The dashboard remains read-mostly /
proposal-only: it surfaces the counts and the explicit commands to act on them,
and exposes no destructive control.

For future GUI work, `engram dashboard --json` also exposes:

```
next_action: {code, command, count, reason}
actions[]:   {code, label, command, count, risk, executes=false}
```

These are metadata-only action descriptors. They are safe for a UI to render,
but they do not execute commands and do not add one-click mutation.

## 5. Telemetry dashboard password rotation helper

Remote dashboard password rotation is handled by:

```
powershell -File ./scripts/rotate_telemetry_dashboard_password.ps1 -Generate
powershell -File ./scripts/rotate_telemetry_dashboard_password.ps1 -Generate -Apply
```

The helper is owner-handoff first: it prints the generated/supplied
`DASH_PASSWORD` so the owner can record it privately, then writes the Cloudflare
Worker secret only when `-Apply` is present. Generated passwords are shell-safe
(`A-Z`, `a-z`, `0-9`, `_`, `-`) and the apply path uses no-newline stdin to
avoid accidentally storing a trailing newline or an empty secret.

Tests: `tests/test_owner_dashboard.py` (readiness present/ reflects reports/
GUI-safe action metadata/ rendered metadata-only/ CLI end-to-end) and
`tests/test_telemetry_dashboard_password_rotation.py`.

## 6. What is intentionally NOT here

- No new agent-facing MCP apply tool (all surfaces are owner CLI).
- No telemetry schema/event expansion.
- No permission/governance enforcement change.
- No hard delete (merge and lifecycle are reversible soft archives; reconcile is
  import-only).
- No release prep, version bump, tag, or publish.
