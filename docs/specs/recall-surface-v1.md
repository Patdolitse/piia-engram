# Recall Surface v1 — single-call recall (spec)

Status: **spec only.** No broad refactor. This defines a single, stronger recall
contract that *composes existing tools* rather than replacing them. Existing
tools stay; the new surface is additive and backward-compatible.

## 1. Problem

Today recall is spread across several tools (mapped in the explore):

- `get_resume_brief` — closest to one-call: identity + project + daily log +
  recent context + top lessons/decisions + suggested docs.
- `get_user_context` — identity/preferences/standards, level-aware.
- `get_relevant_knowledge` — project-relevant lessons (no query).
- `search_knowledge` — keyword search across knowledge.
- `get_recent_context`, `get_project_context`, `get_daily_log` — supporting.

For "cold start, tell me who I am and where we left off, focused on X," a caller
often needs `get_resume_brief` **plus** `search_knowledge(X)` — two calls — and
gets no freshness/provenance signal on the returned knowledge.

## 2. Goal

One call that returns a stable, predictable recall payload:

```text
1. identity/profile slice   — stable across calls (who the user is, how they work)
2. recent activity          — what happened lately (sessions / daily log digest)
3. relevant knowledge       — project-relevant AND optional query-focused
4. provenance/freshness     — per knowledge item: source_agent + freshness hint
```

This is the "stronger single-call product surface" called out in the competitor
decision. It does NOT remove the granular tools — power users and hooks still use
them.

## 3. Proposed contract

A read-only tool/helper (working name `get_recall` / `recall_brief`) with a
stable shape:

```json
{
  "identity": {
    "role": "...", "language": "...", "technical_level": "...",
    "preferences_digest": ["..."], "quality_standards": ["..."]
  },
  "recent_activity": {
    "last_session": {"tool": "...", "when": "...", "summary": "..."},
    "daily_log_digest": ["..."]
  },
  "knowledge": [
    {
      "type": "lesson|decision",
      "summary": "...",                     // or question/choice for decisions
      "domain": "...",
      "provenance": {"source_agent": "...", "run_id": "...",
                     "last_validated_at": "..."},
      "freshness": {"freshness_status": "fresh|aging|stale|unknown",
                    "age_days": 12.4, "basis": "last_reviewed"}
    }
  ],
  "meta": {"project": "...", "query": "...", "token_budget": 2000,
           "governance": {"trust_level": "...", "excluded_count": 0}}
}
```

### Inputs

```text
project_folder : str  = ""     # scope to a project (like get_resume_brief)
query          : str  = ""     # optional keyword focus (folds in search_knowledge)
token_budget   : int  = 2000   # same budgeting model as get_resume_brief
include_freshness : bool = True # attach provenance/freshness hints (Task 3)
```

### Composition (no new retrieval logic)

```text
identity         := get_user_context(level="quick") slice
recent_activity  := get_recent_context + get_daily_log digest
knowledge        := get_relevant_knowledge(project) ∪ (search_knowledge(query) if query)
                    de-duplicated by id, ranked, trimmed to token_budget
freshness        := provenance.annotate_freshness(knowledge)   # already implemented
governance meta  := describe_caller_permissions(...)           # already implemented
```

Every input is an existing capability; v1 is an *aggregator + annotator*, not new
ranking.

## 4. Backward compatibility

- Purely additive: a new tool/helper. `get_resume_brief`, `get_user_context`,
  etc. are unchanged.
- Reuses the existing token-budget and governance gating, so disclosure rules are
  identical to the underlying tools (no new leakage path).
- `include_freshness=True` uses the already-shipped, non-destructive
  `provenance.annotate_freshness`; setting it False yields a payload with no
  freshness keys (strict subset).

## 5. Why not refactor the existing tools

The granular tools are used directly by hooks (`auto_inject_resume_brief` calls
`get_resume_brief`) and by power callers. Collapsing them risks those paths.
v1 adds a convenience surface on top and leaves the primitives intact; if usage
shows the aggregator dominates, deprecation can be considered later — separately.

## 6. Implementation plan (when approved)

1. ✅ **Implemented** — pure aggregator helper `src/piia_engram/recall.py`
   (`build_recall_payload`) takes already-loaded sub-results and assembles the
   payload, de-duplicates by id, projects each item to summary/metadata (never
   raw stored dicts), applies `annotate_freshness` (opt-in), and trims to a token
   budget. Unit-tested store-free in `tests/test_recall.py`.
2. ✅ **Implemented (owner-context CLI only)** — `src/piia_engram/recall_service.py`
   (`gather_recall` + `render_recall_text`) fetches the sub-results from a live
   `Engram` through existing governed read methods, optionally collapses
   superseded versions to HEAD (`version_chain.collapse_to_heads`), and feeds
   the pure aggregator. Surfaced as **`engram recall`** (CLI = `private-self`
   owner), so it adds no new agent-facing disclosure surface. Tested store-free
   with a duck-typed fake in `tests/test_recall_service.py`, plus a metadata-only
   quality harness in `tests/test_recall_quality.py`.
3. **Deferred (still review-gated)** — the thin **MCP** tool that exposes recall
   to *agents*. It touches MCP output and overlaps `get_resume_brief`, so it
   needs its own governance/leak-matrix review before shipping (see §3 note).
   The CLI path above is owner-only and does not substitute for that review.
4. Add to Tier-1 only after it proves out (it would overlap `get_resume_brief`).

### Version-chain read scaffold (Phase 6)

`src/piia_engram/version_chain.py` is a pure read/report layer over the typed
`supersedes` / `led_to` / `implemented_by` edges already produced by
`decision_thread`: `resolve_heads`, `collapse_to_heads` (default-recall "prefer
HEAD"), `lineage` (full history walk), and `build_version_report` (metadata-only
per-topic report). It reads no store and writes nothing; the richer write-path
version fields (`parent_id`/`root_id`/`derives_from`) in
`knowledge-version-chain-design.md` remain deferred. Tested in
`tests/test_version_chain.py`.

## 7. Non-goals

- No new ranking/embedding model — composes existing retrieval.
- No change to stored data.
- Not a replacement for `get_resume_brief` in v1.
