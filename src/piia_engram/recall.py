"""Recall Surface v1 — pure aggregator helper (see docs/specs/recall-surface-v1.md).

This is step 1 of the recall surface: a *pure* function that assembles a stable,
predictable recall payload from already-loaded sub-results (identity slice,
recent-activity digest, project-relevant knowledge, optional query knowledge).
It composes existing capabilities; it introduces **no new retrieval/ranking** and
does **not** touch the store.

The thin MCP tool that gathers the sub-results and calls this aggregator is
implemented as ``get_recall``. Governance-enabled non-owner callers are refused
before the gather layer runs because this aggregate surface overlaps the
owner-only resume brief and can combine several knowledge classes.

Design constraints:
- stdlib only, side-effect free, safe to unit-test with fixtures.
- knowledge items are *projected* to summary/metadata — never raw stored dicts —
  so internal bookkeeping fields cannot leak through the recall surface.
- freshness/provenance are attached via the already-shipped ``provenance`` helper
  and are opt-in (``include_freshness``); turning it off yields a strict subset.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import provenance as _provenance

# Rough chars-per-token estimate used only for trimming to ``token_budget``. The
# real tokenizer is the caller's model; this is intentionally conservative and
# matches the coarse budgeting model the resume brief already uses.
_CHARS_PER_TOKEN = 4


def _entry_type(entry: dict[str, Any]) -> str:
    return "decision" if ("choice" in entry or "question" in entry) else "lesson"


def _dedup_key(entry: dict[str, Any], index: int) -> str:
    eid = entry.get("id")
    if isinstance(eid, str) and eid.strip():
        return eid.strip()
    # No id (e.g. a projected/legacy item): fall back to identity text so two
    # copies of the same knowledge still collapse, but distinct items don't.
    text = entry.get("summary") or entry.get("question") or entry.get("choice") or ""
    return f"__noid__:{index}:{str(text)[:120]}"


def _project_item(
    entry: dict[str, Any], *, include_freshness: bool, now: datetime | None
) -> dict[str, Any]:
    """Project a stored knowledge dict to the stable recall view (summary/meta)."""
    etype = _entry_type(entry)
    view: dict[str, Any] = {"type": etype}
    if etype == "decision":
        view["question"] = entry.get("question", "") or ""
        view["choice"] = entry.get("choice", "") or ""
    else:
        view["summary"] = entry.get("summary", "") or ""

    domain = entry.get("domain")
    if isinstance(domain, str) and domain.strip():
        view["domain"] = domain.strip()

    # Provenance subset — source-explainable, never internal bookkeeping.
    prov: dict[str, Any] = {}
    source_agent = _provenance.resolve_source_agent(entry)
    if source_agent:
        prov["source_agent"] = source_agent
    raw_prov = entry.get("provenance")
    if isinstance(raw_prov, dict):
        for key in ("run_id", "last_validated_at"):
            value = raw_prov.get(key)
            if isinstance(value, str) and value.strip():
                prov[key] = value.strip()
    if prov:
        view["provenance"] = prov

    if include_freshness:
        view["freshness"] = _provenance.compute_freshness(entry, now=now)
    return view


def _item_cost(view: dict[str, Any]) -> int:
    """Approximate token cost of a projected item."""
    return max(1, len(json.dumps(view, ensure_ascii=False)) // _CHARS_PER_TOKEN)


def merge_knowledge(
    relevant: list[dict[str, Any]] | None,
    query_knowledge: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """De-duplicate by id, relevant-first then query-only, preserving order.

    Pure: returns the original (un-projected) entry dicts in merged order so the
    caller can still inspect raw fields; ``build_recall_payload`` projects them.
    """
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in (relevant or [], query_knowledge or []):
        for index, entry in enumerate(source):
            if not isinstance(entry, dict):
                continue
            key = _dedup_key(entry, index)
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def build_recall_payload(
    *,
    identity: dict[str, Any] | None = None,
    recent_activity: dict[str, Any] | None = None,
    relevant_knowledge: list[dict[str, Any]] | None = None,
    query_knowledge: list[dict[str, Any]] | None = None,
    project: str = "",
    query: str = "",
    token_budget: int = 2000,
    include_freshness: bool = True,
    governance: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the Recall Surface v1 payload from already-loaded sub-results.

    All inputs are pre-fetched by the (future, reviewed) caller; this function
    only assembles, de-duplicates, projects, annotates, and trims. It never
    reads the store and never mutates its inputs.
    """
    merged = merge_knowledge(relevant_knowledge, query_knowledge)

    knowledge: list[dict[str, Any]] = []
    spent = 0
    excluded = 0
    budget = max(0, int(token_budget))
    for entry in merged:
        view = _project_item(entry, include_freshness=include_freshness, now=now)
        cost = _item_cost(view)
        # Always allow at least one item through so a tiny budget never yields an
        # empty knowledge list when there is something to say.
        if knowledge and spent + cost > budget:
            excluded += 1
            continue
        knowledge.append(view)
        spent += cost

    gov_meta: dict[str, Any] = {"excluded_count": excluded}
    if isinstance(governance, dict):
        trust = governance.get("trust_level")
        if trust is not None:
            gov_meta["trust_level"] = trust

    return {
        "identity": dict(identity) if isinstance(identity, dict) else {},
        "recent_activity": dict(recent_activity)
        if isinstance(recent_activity, dict)
        else {},
        "knowledge": knowledge,
        "meta": {
            "project": project,
            "query": query,
            "token_budget": budget,
            "governance": gov_meta,
        },
    }
