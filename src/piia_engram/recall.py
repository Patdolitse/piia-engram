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
    if "choice" in entry or "question" in entry:
        return "decision"
    # v4.20 playbook pointer: playbook entries carry title/triggers and never a
    # lesson summary — mis-projecting them produced an empty-summary "lesson".
    if "triggers" in entry or ("title" in entry and "summary" not in entry):
        return "playbook"
    return "lesson"


# v4.20 frozen playbook projection bounds (PROPOSAL_v420_v2): metadata-only —
# `steps` NEVER enters a recall payload; description capped at 240 chars so a
# pointer stays a pointer; at most 5 trigger hints with per-item caps.
_PLAYBOOK_DESCRIPTION_CAP = 240
_PLAYBOOK_TRIGGER_MAX_ITEMS = 5
_PLAYBOOK_TRIGGER_ITEM_CAP = 60
_PLAYBOOK_MAX_ITEMS = 2
_PLAYBOOK_BUDGET_SHARE = 0.25


def _project_playbook_item(
    entry: dict[str, Any],
    *,
    include_freshness: bool,
    now: datetime | None,
    include_trust: bool = False,
) -> dict[str, Any]:
    """Project a playbook to the stable recall view (pointer, never the body)."""
    view: dict[str, Any] = {"type": "playbook"}
    eid = entry.get("id")
    if isinstance(eid, str) and eid.strip():
        view["id"] = eid.strip()
    view["title"] = str(entry.get("title", "") or "")[:200]
    triggers = entry.get("triggers")
    if isinstance(triggers, list) and triggers:
        hints = [str(t).strip()[:_PLAYBOOK_TRIGGER_ITEM_CAP] for t in triggers if str(t).strip()]
        if hints:
            view["triggers"] = hints[:_PLAYBOOK_TRIGGER_MAX_ITEMS]
    domain = entry.get("domain")
    if isinstance(domain, str) and domain.strip():
        view["domain"] = domain.strip()[:80]
    description = entry.get("description")
    if isinstance(description, str) and description.strip():
        view["description"] = description.strip()[:_PLAYBOOK_DESCRIPTION_CAP]
    if isinstance(entry.get("version"), int):
        view["version"] = entry["version"]
    updated = entry.get("last_updated") or entry.get("updated_at") or entry.get("timestamp")
    if isinstance(updated, str) and updated:
        view["updated_at"] = updated

    prov = _provenance.project_recall_provenance(entry)
    if prov:
        view["provenance"] = prov
    if include_freshness:
        view["freshness"] = _provenance.compute_freshness(entry, now=now)
    labeling = _project_labeling(entry)
    if labeling:
        view["labeling"] = labeling
    if include_trust:
        trust = _project_trust(entry, freshness=view.get("freshness"), now=now)
        if trust:
            view["trust"] = trust
    return view


def _dedup_key(entry: dict[str, Any], index: int) -> str:
    eid = entry.get("id")
    if isinstance(eid, str) and eid.strip():
        return eid.strip()
    # No id (e.g. a projected/legacy item): fall back to identity text so two
    # copies of the same knowledge still collapse, but distinct items don't.
    text = entry.get("summary") or entry.get("question") or entry.get("choice") or ""
    return f"__noid__:{index}:{str(text)[:120]}"


def _project_item(
    entry: dict[str, Any],
    *,
    include_freshness: bool,
    now: datetime | None,
    include_trust: bool = False,
) -> dict[str, Any]:
    """Project a stored knowledge dict to the stable recall view (summary/meta)."""
    etype = _entry_type(entry)
    if etype == "playbook":
        return _project_playbook_item(
            entry,
            include_freshness=include_freshness,
            now=now,
            include_trust=include_trust,
        )
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
    prov = _provenance.project_recall_provenance(entry)
    if prov:
        view["provenance"] = prov

    if include_freshness:
        view["freshness"] = _provenance.compute_freshness(entry, now=now)
    labeling = _project_labeling(entry)
    if labeling:
        view["labeling"] = labeling
    if include_trust:
        trust = _project_trust(entry, freshness=view.get("freshness"), now=now)
        if trust:
            view["trust"] = trust
    return view


def _project_trust(
    entry: dict[str, Any], *, freshness: dict[str, Any] | None, now: datetime | None
) -> dict[str, Any]:
    """Owner-only allowlisted trust block: why-trustworthy / anchor / expires /
    validated-at. `expires` is derived from freshness (trigger-bound/skip_decay
    facts don't expire on a clock; time facts age) -- never a fabricated date."""
    return _provenance.project_trust(entry, freshness=freshness, now=now)


def _item_cost(view: dict[str, Any]) -> int:
    """Approximate token cost of a projected item."""
    return max(1, len(json.dumps(view, ensure_ascii=False)) // _CHARS_PER_TOKEN)


def _count_dicts(items: list[dict[str, Any]] | None) -> int:
    return sum(1 for item in (items or []) if isinstance(item, dict))


def _project_labeling(entry: dict[str, Any]) -> dict[str, Any]:
    return _provenance.project_labeling(entry)


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
    playbooks: list[dict[str, Any]] | None = None,
    project: str = "",
    query: str = "",
    token_budget: int = 2000,
    include_freshness: bool = True,
    include_trust: bool = False,
    governance: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the Recall Surface v1 payload from already-loaded sub-results.

    All inputs are pre-fetched by the (future, reviewed) caller; this function
    only assembles, de-duplicates, projects, annotates, and trims. It never
    reads the store and never mutates its inputs.

    v4.20: ``playbooks`` is an OPTIONAL pre-fetched playbook bucket (pointers
    only, projected metadata-only). It shares the knowledge budget under a
    sub-cap — at most ``_PLAYBOOK_MAX_ITEMS`` items and at most
    ``_PLAYBOOK_BUDGET_SHARE`` of the budget — so playbooks can be surfaced
    without ever starving lessons/decisions of capacity.
    """
    merged = merge_knowledge(relevant_knowledge, query_knowledge)

    knowledge: list[dict[str, Any]] = []
    spent = 0
    excluded = 0
    budget = max(0, int(token_budget))
    playbook_token_cap = int(budget * _PLAYBOOK_BUDGET_SHARE)
    playbook_count = 0
    playbook_spent = 0

    playbook_views: list[dict[str, Any]] = []
    playbook_excluded = 0
    for entry in (playbooks or []):
        if not isinstance(entry, dict):
            continue
        if playbook_count >= _PLAYBOOK_MAX_ITEMS:
            playbook_excluded += 1
            continue
        view = _project_item(
            entry, include_freshness=include_freshness, now=now, include_trust=include_trust
        )
        cost = _item_cost(view)
        if playbook_count >= 1 and playbook_spent + cost > playbook_token_cap:
            playbook_excluded += 1
            continue
        playbook_views.append(view)
        playbook_count += 1
        playbook_spent += cost

    # lessons/decisions spend against the budget MINUS the playbook reservation
    # (anti-starve runs both ways: playbooks cannot crowd out knowledge, and
    # knowledge cannot consume the playbooks' sub-cap).
    lesson_budget = max(0, budget - playbook_spent)
    for entry in merged:
        view = _project_item(
            entry, include_freshness=include_freshness, now=now, include_trust=include_trust
        )
        cost = _item_cost(view)
        # Always allow at least one item through so a tiny budget never yields an
        # empty knowledge list when there is something to say.
        if knowledge and spent + cost > lesson_budget:
            excluded += 1
            continue
        knowledge.append(view)
        spent += cost

    # playbook pointers ride at the end of the knowledge list
    knowledge.extend(playbook_views)
    total_spent = spent + playbook_spent

    gov_meta: dict[str, Any] = {"excluded_count": excluded}
    if isinstance(governance, dict):
        trust = governance.get("trust_level")
        if trust is not None:
            gov_meta["trust_level"] = trust

    context_usage = {
        "sources": {
            "project_relevant": {"loaded": _count_dicts(relevant_knowledge)},
            "query": {"loaded": _count_dicts(query_knowledge)},
            "playbooks": {"loaded": _count_dicts(playbooks)},
        },
        "knowledge": {
            "merged": len(merged),
            "returned": len(knowledge),
            "trimmed_by_budget": excluded + playbook_excluded,
        },
        "playbooks": {
            "returned": playbook_count,
            "trimmed": playbook_excluded,
            "budget_share_cap_tokens": playbook_token_cap,
        },
        "budget": {
            "requested_tokens": budget,
            "estimated_used_tokens": total_spent,
            "over_budget": total_spent > budget if budget else bool(total_spent),
        },
        "freshness": {
            "attached": sum(
                1 for item in knowledge if isinstance(item.get("freshness"), dict)
            ),
        },
        "provenance": {
            "with_source_agent": sum(
                1
                for item in knowledge
                if isinstance(item.get("provenance"), dict)
                and item["provenance"].get("source_agent")
            ),
        },
    }

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
            "context_usage": context_usage,
        },
    }
