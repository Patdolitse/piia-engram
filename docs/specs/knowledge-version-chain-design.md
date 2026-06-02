# Knowledge Version Chain — design (spec only)

Status: **design / spec only.** No data migration in this pass. The goal is to
let knowledge *evolve* (supersede, refine, branch) while keeping history, so
recall can prefer the current version and still explain its lineage. This is the
"knowledge evolution / version-chain slice" from the competitor decision.

## 1. What exists today (build on)

- Decisions already carry an optional `supersedes` field, and there is a
  `RelationStore` (`governance_store.py`) holding typed, directed edges
  (decision threads), plus `decision_thread.py`.
- Entries are plain dicts; storage preserves unknown keys (no migration needed
  for *additive* fields) — see the Provenance Contract notes.
- Entries already have a stable 12-char `id`.

So Engram already has the substrate for edges; version-chain v1 standardizes the
field set and the resolution rules across all knowledge types.

## 2. Fields (additive, optional)

| Field | Type | Meaning |
|-------|------|---------|
| `parent_id` | str | The immediate predecessor this entry was edited/derived from. |
| `root_id` | str | The origin of the chain (stable across all versions). Defaults to the entry's own id when it starts a chain. |
| `supersedes` | str \| str[] | Entry id(s) this entry replaces (already used for decisions; generalize to lessons/playbooks). |
| `derives_from` | str \| str[] | Weaker link: this entry was *informed by* another but does not replace it (branch, not supersede). |
| `updated_by` | str | The agent/actor that produced this version (aligns with provenance `source_agent`). |

All optional; absence means "standalone entry, no chain" — fully backward
compatible.

### Invariants

```text
V1  root_id is immutable once set; every version in a chain shares one root_id.
V2  parent_id points to exactly one predecessor (or is absent for a root).
V3  supersedes implies the superseded entry is marked status="outdated"
    (not deleted) — history is retained.
V4  No cycles: parent_id / supersedes / derives_from must form a DAG.
    A creation that would introduce a cycle is rejected.
V5  derives_from never changes the target's status (it is a soft link).
```

## 3. Two representations (and why)

Edges can live **inline on the entry** (`parent_id`, `root_id`, `supersedes`,
`derives_from`) AND/OR in the `RelationStore`. Recommendation:

- **Inline** for the linear lineage (`parent_id`, `root_id`, `supersedes`) —
  cheap to read with the entry, which is what recall needs.
- **RelationStore** for richer/branching edges (`derives_from`, cross-type
  links) — keeps the entry small and reuses the existing typed-edge store.

This mirrors what already exists (decisions inline `supersedes` + RelationStore
threads) instead of inventing a third mechanism.

## 4. Recall resolution

When recall surfaces a chain:

```text
- Prefer the HEAD of the chain (no entry supersedes it, status active).
- Collapse superseded versions out of default recall (status=outdated).
- On request (e.g. "history of X"), walk parent_id/root_id to show lineage,
  newest→oldest, each with its freshness hint (Task 3).
```

So default recall stays clean (one current version), and provenance/version
history is available on demand — without deleting anything.

## 5. Write paths (future)

```text
update_lesson / update_decision (exist today) → when content materially changes,
  optionally create a NEW version: copy → new id → parent_id=old, root_id=old.root,
  supersedes=[old], updated_by=caller; mark old status=outdated.
  Default behavior stays in-place edit; versioning is opt-in per update.
```

This keeps the common case (small fix) cheap and only forks history when asked.

## 6. Migration — explicitly deferred, with rules

No migration now. When/if backfilling existing data:

```text
- Dry-run first: report how many entries would get root_id = self id.
- Backfill is additive only (set root_id := id where absent). Never rewrite ids.
- Existing `supersedes` on decisions is preserved as-is.
- Full backup before any write; provide a rollback (restore from backup).
- Gate behind tests proving idempotency (running twice changes nothing).
```

This satisfies the "do not migrate existing data without tests and clear
rollback" constraint.

## 7. Test plan (when implemented)

```text
- DAG/cycle rejection (V4).
- root_id immutability across N versions (V1).
- supersede marks predecessor outdated, retains it (V3).
- recall returns only the head by default; history walk returns full lineage.
- backward compat: entries with none of the fields recall unchanged.
- backfill idempotency + dry-run counts.
```

## 8. Non-goals

- No automatic merge/conflict resolution of versions (human decides).
- No deletion of history as part of versioning.
- Not coupled to the version-chain of the *package* — this is knowledge lineage.
