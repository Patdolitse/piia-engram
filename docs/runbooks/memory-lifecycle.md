# Runbook: Memory Lifecycle, Decay & Scale (Phase 7)

Status: **implemented (scoring + proposal) + characterization tests.** This
covers how Engram degrades gracefully as the store grows. The scoring and the
archive/prune proposal are live (`src/piia_engram/lifecycle.py`, surfaced as
`engram lifecycle`). Acting on a proposal remains an explicit, owner-confirmed
step — see the never-auto-delete contract below.

## 0. The one invariant that never bends

> **Engram never automatically deletes user memory.**

`lifecycle.py` computes scores and proposals only. It performs no archival, no
deletion, no mutation. Acting on a proposal is a separate, explicit, owner-gated
action (the management surface's `--apply`, with confirmation). This is enforced
by construction (the module has no write path) and by tests
(`tests/test_lifecycle.py::test_never_auto_delete_invariant_present`,
`::test_synthetic_scale_proposal_path` asserts inputs are untouched at scale).

## 1. Decay scoring (metadata only)

`score_entry(entry, now=...)` returns a `decay_score` in `[0, 1]` (higher = more
decayed) from **metadata only** — never the stored body:

| Factor | Weight | Source |
|--------|--------|--------|
| Freshness (`fresh`/`aging`/`stale`/`unknown`) | 0.0 / 0.35 / 0.6 / 0.3 | `provenance.compute_freshness` (age basis: `last_validated_at` → `last_reviewed` → `created_at` → `timestamp`) |
| Access count (< 5) | up to 0.25 | `access_count` (0 → full weight) |
| Staging (un-promoted) | 0.15 | `tier == "staging"` |
| Fails structural quality gate | 0.10 | `quality_eval.evaluate_candidate` |

`unknown` freshness contributes a mild 0.3 — never decisive — so an entry with
sparse metadata can never be escalated to a prune proposal on age alone.

## 2. Proposal mapping (conservative by design)

`build_lifecycle_proposal(entries, now=...)` maps each score to a proposed action:

| Score | Proposal | Meaning |
|-------|----------|---------|
| `< 0.55` | `keep` | healthy; leave it |
| `0.55 – 0.8` | `archive_candidate` | propose archiving (reversible status change) |
| `>= 0.8` **and** staging **and** never accessed | `prune_candidate` | propose pruning — the *only* bucket that suggests deletion, and only for un-promoted, never-used notes |
| `>= 0.8` otherwise | `review` | high decay but verified/used → surface for human review, never propose deletion |

Verified or ever-accessed knowledge is therefore **never** proposed for pruning,
only for review. The report is sorted most-decayed-first and is metadata-only
(ids, types, scores, reason codes — no summaries/choices/bodies).

## 3. Owner workflow

```bash
engram lifecycle            # human-readable proposal (nothing changes)
engram lifecycle --json     # machine-readable proposal
```

To act on a candidate, use the existing review/management surface explicitly:

```bash
engram review show <id>           # inspect one item
engram review archive <id> --yes  # archive (reversible), owner-confirmed
```

There is intentionally no `engram lifecycle --apply`. Lifecycle scoring proposes;
the owner disposes, item by item, through the audited review path.

## 4. Scale posture

`build_lifecycle_proposal` is pure and O(n) over entries; the synthetic-scale
test exercises 5,000 mixed entries and asserts the proposal path classifies all
of them while mutating none. For very large stores, callers should page entries
(e.g. score per dataset) rather than expecting lifecycle to manage paging.

## 5. Out of scope (this pass)

- Automatic deletion (forbidden — see §0).
- Moving or rewriting real user data.
- Remote archival / off-device tiering.
- Embedding- or semantics-based decay (scoring is metadata-only by design).
