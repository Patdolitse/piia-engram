# Provenance & Freshness Contract v1

Status: **v1 implemented as additive metadata + governed recall/review
surfacing.** The helper module (`src/piia_engram/provenance.py`), write-path
normalization, opt-in freshness annotation, and validation stamping through
review/promote paths are shipped. The default legacy read shapes remain
compatible; richer provenance/freshness/labeling is additive or opt-in.

## 1. Goal

Make recall **source-explainable** and **freshness-aware** without breaking
backward compatibility with existing knowledge JSON. A reader (human or AI)
should be able to answer:

- *Where did this come from?* — which agent, in which run, when last validated.
- *Can I still trust it?* — is it fresh, aging, or stale?

## 2. Fields (all optional, additive)

These live inside each knowledge entry's existing `provenance` object (lessons,
decisions, playbooks). All are optional; entries without them remain valid.

| Field | Type | Meaning | Backfill |
|-------|------|---------|----------|
| `source_agent` | string | The agent/tool identity that produced or last validated the entry (e.g. `claude_code`, `codex`, `cursor`). Distinct from `source_tool` only in that it may name a sub-agent or run actor. | falls back to `source_tool` when absent |
| `run_id` | string | Identifier of the workflow/session run that produced the entry. Lets multiple entries be traced to one run. | none (absent = unknown run) |
| `last_validated_at` | ISO-8601 string | When a human/agent last confirmed the entry still holds. Distinct from `created_at` (birth) and `last_reviewed` (existing field). | falls back to `last_reviewed`, then `created_at`, then `timestamp` |

`freshness_status` is **derived at recall time, never stored** (see §3) so it
cannot go stale on disk.

Each knowledge entry also carries a system-derived `labeling` object:

```json
{
  "source_kind": "human | agent | imported | unknown",
  "annotation_quality": "raw | partial | mature",
  "validation_state": "unreviewed | validated | needs_review",
  "signals": ["has_source_agent", "has_domain", "needs_owner_review"]
}
```

`labeling` is **not caller-certified**. Agent-facing MCP payloads have any
incoming `labeling` removed with the other trust fields, then Engram derives it
from provenance, domain/project/source URL context, risk tier, and approval
state. Staging entries are `needs_review`; high-risk entries remain conservative
even after promotion, while ordinary owner-reviewed/promoted entries receive a
validation stamp and can become `validated`/`mature` when the supporting signals
are present.

### Type / safety rules

- `source_agent`, `run_id` are short identifiers: trimmed, capped at 120 chars,
  rejected (dropped) if they contain newlines or look like content/paths.
- `last_validated_at` must parse as ISO-8601 (with or without `Z`); otherwise it
  is ignored and freshness falls back to the next basis.
- None of these fields are sensitive by themselves, but `source_agent`/`run_id`
  pass through the **same sensitivity gate** as the rest of the entry — they are
  never surfaced for an entry the caller is not allowed to see.

## 3. Freshness annotation (recall-time, derived)

`compute_freshness(entry, now)` returns a non-destructive annotation:

```json
{
  "freshness_status": "fresh | aging | stale | unknown",
  "age_days": 12.4,
  "basis": "last_validated_at | last_reviewed | created_at | timestamp | none",
  "as_of": "<ISO timestamp the age was measured against>"
}
```

Thresholds (v1, conservative):

```text
fresh    age <= 30 days
aging    30 < age <= 90 days
stale    age  > 90 days
unknown  no parseable timestamp on any basis
```

Basis selection priority (first that parses wins):
`last_validated_at` → `last_reviewed` → `created_at` → `timestamp`.

`annotate_freshness(items)` returns the same list with a `freshness` key added to
each item. It is **pure and non-destructive**: it copies, never mutates the
stored dict, and an entry with no timestamps yields `freshness_status:"unknown"`
rather than an error.

## 4. Backward compatibility

- Storage already preserves unknown keys (entries are plain dicts; `_read_json`
  → `_ensure_fields` only adds, never drops — see `core.py:_ensure_fields`,
  `storage.py:_read_json`). New optional fields require **no migration**.
- Old entries without provenance fields:
  - `source_agent` resolves to `source_tool`,
  - `run_id` is reported as absent,
  - freshness uses `last_reviewed`/`created_at` (which existing entries have).
- The annotation is additive on read; removing it restores the exact prior
  output shape.

## 5. Source-explainable recall behavior (contract)

Recall surfaces (`get_relevant_knowledge`, `search_knowledge`, `get_recall`,
and the resume/recall brief where applicable) can attach, per returned item:

```json
"provenance": { "source_agent": "...", "run_id": "...", "last_validated_at": "..." },
"freshness":  { "freshness_status": "aging", "age_days": 47.1, "basis": "last_reviewed" },
"labeling":   { "validation_state": "validated", "annotation_quality": "mature" }
```

so an AI can say *"this decision came from the codex run on 2026-05-02 and is
aging (47 days since last validation)"* instead of presenting all memory as
equally current. This is opt-in at the surface level and changes no stored data.

## 6. Rollout plan

1. **Now (this contract):** `provenance.py` helper (pure functions) + tests.
   No behavior change to existing tools.
2. ✅ **Follow-up A (write path) — implemented:** `add_lesson` / `add_decision` /
   `add_playbook` accept optional `source_agent` / `run_id` / `last_validated_at`
   and normalize them into `provenance` via `mcp_server._attach_provenance`.
   Omitting them leaves the entry's `provenance` unchanged. Covered by
   `tests/test_provenance_wiring.py`.
3. ✅ **Follow-up B (recall path) — implemented:** `search_knowledge` and
   `get_relevant_knowledge` take an opt-in `include_freshness=False`; when true
   they call `annotate_freshness` **after** governance filtering, so the default
   output is byte-identical and a non-owner can never have an above-ceiling item
   annotated. Covered by `tests/test_provenance_wiring.py`.
4. ✅ **Follow-up C (validation tooling) — implemented:** the core
   `mark_validated_knowledge` primitive stamps `last_validated_at` +
   `source_agent` and refreshes derived `labeling`; `review_knowledge` and
   `promote_knowledge` call it through the existing review/promote paths.
   Covered by `tests/test_data_labeling.py`.

## 7. Non-goals (v1)

- No cryptographic provenance / signing (identity over MCP is self-reported;
  see governance notes). This is descriptive metadata, not attestation.
- No automatic re-validation or expiry of stale knowledge — freshness is a
  *hint*, never an automatic delete.

## 8. Deferred / future

The following items are known and intentionally deferred, not omissions in the
current source-aware freshness scope:

- Monorepo anchor scope: anchors currently bind to one project identity instead
  of per-package or per-subtree scopes.
- Repository rename or alias migration: renamed repos may need an explicit
  alias/adoption workflow.
- Broader dependency ecosystems: dependency anchors currently cover npm,
  Python, and file-backed references only.
- Doctor-driven anchor validation: `doctor` does not yet run automatic anchor
  revalidation.
- Non-owner MCP anchor writes: anchor status writeback remains owner/internal
  only; non-owner agents cannot stamp or repair anchor trust fields.
