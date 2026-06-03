"""Recall service (Phase 6) — gather + render around the pure aggregator.

:mod:`recall.build_recall_payload` is a *pure* assembler that takes already
loaded sub-results. This module is the thin **owner-context gather layer** that
fetches those sub-results from a live :class:`Engram` through its existing,
already-governed read methods, optionally collapses superseded versions to HEAD
(:mod:`version_chain`), and renders a human-readable digest for the CLI.

Why CLI / owner-context only (no new MCP tool here):
    The agent-facing MCP recall tool is *deliberately deferred* in
    ``docs/specs/recall-surface-v1.md`` §6 step 2 — it touches MCP output and
    overlaps ``get_resume_brief``, so it needs its own governance/leak-matrix
    review before shipping. The CLI (``engram recall``) runs as the owner
    (``private-self``) over the user's own store, so it adds no new agent-facing
    disclosure surface. It composes only existing read methods; it introduces no
    new retrieval or ranking.

The gather layer is duck-typed against the Engram read API and defensive: a
missing/empty sub-result degrades to an empty slice rather than raising, so the
recall digest is always producible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import recall as _recall
from . import version_chain as _vc

# Profile fields surfaced in the identity slice, in display order. Kept to the
# stable "who the user is / how they work" set the recall spec calls out; never
# dumps the whole profile (which may carry restricted fields).
_IDENTITY_FIELDS = ("role", "language", "technical_level")
_IDENTITY_LIST_FIELDS = ("preferences", "quality_standards", "work_patterns")


def _identity_slice(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Project a (safe) profile dict to the stable recall identity slice."""
    if not isinstance(profile, dict):
        return {}
    out: dict[str, Any] = {}
    for field in _IDENTITY_FIELDS:
        value = profile.get(field)
        if isinstance(value, str) and value.strip():
            out[field] = value.strip()
    for field in _IDENTITY_LIST_FIELDS:
        value = profile.get(field)
        if isinstance(value, list) and value:
            # keep only short scalar entries; never echo nested structures
            digest = [str(v).strip() for v in value if str(v).strip()]
            if digest:
                out[field] = digest
    return out


def _recent_activity(recent: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Project recent-context records to a compact, content-free-ish digest.

    Uses only ``tool`` / ``session_id`` / ``modified_at`` and a short summary
    line if the record carries one — never the full saved markdown body.
    """
    if not recent:
        return {}
    latest = recent[0]
    if not isinstance(latest, dict):
        return {}
    activity: dict[str, Any] = {}
    tool = latest.get("tool")
    if isinstance(tool, str) and tool:
        activity["last_tool"] = tool
    when = latest.get("modified_at")
    if isinstance(when, str) and when:
        activity["when"] = when
    session_id = latest.get("session_id")
    if isinstance(session_id, str) and session_id:
        activity["session_id"] = session_id
    return activity


def gather_recall(
    eng: Any,
    *,
    project_folder: str = "",
    query: str = "",
    limit: int = 8,
    token_budget: int = 2000,
    include_freshness: bool = True,
    collapse_versions: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble a recall payload from a live Engram instance.

    Calls only existing read methods (``get_safe_profile``/``get_profile``,
    ``get_recent_context``, ``get_relevant_lessons``, ``search_knowledge``),
    optionally collapses superseded knowledge to its current head using typed
    relation edges, then delegates to :func:`recall.build_recall_payload`.

    All sub-fetches are guarded: a method that is missing or raises yields an
    empty slice, so the digest is always producible. Returns the standard
    Recall Surface v1 payload plus a ``meta.collapsed_versions`` count.
    """
    # --- identity -------------------------------------------------------
    profile: dict[str, Any] | None = None
    getter = getattr(eng, "get_safe_profile", None) or getattr(eng, "get_profile", None)
    if callable(getter):
        try:
            profile = getter()
        except Exception:  # pragma: no cover - defensive; never block recall
            profile = None
    identity = _identity_slice(profile)

    # --- recent activity ------------------------------------------------
    recent: list[dict[str, Any]] | None = None
    if hasattr(eng, "get_recent_context"):
        try:
            recent = eng.get_recent_context(limit=1)
        except Exception:  # pragma: no cover - defensive
            recent = None
    recent_activity = _recent_activity(recent)

    # --- relevant (project) knowledge -----------------------------------
    relevant: list[dict[str, Any]] = []
    if hasattr(eng, "get_relevant_lessons"):
        try:
            relevant = eng.get_relevant_lessons(
                project_folder=project_folder or None,
                limit=limit,
                _update_access=False,
            ) or []
        except Exception:  # pragma: no cover - defensive
            relevant = []

    # --- query knowledge (optional) -------------------------------------
    query_knowledge: list[dict[str, Any]] = []
    if query and hasattr(eng, "search_knowledge"):
        try:
            hits = eng.search_knowledge(query, scope="all", limit=limit) or {}
        except Exception:  # pragma: no cover - defensive
            hits = {}
        for bucket in ("lessons", "decisions"):
            rows = hits.get(bucket) if isinstance(hits, dict) else None
            if isinstance(rows, list):
                query_knowledge.extend(r for r in rows if isinstance(r, dict))

    # --- version collapse (prefer HEAD) ---------------------------------
    collapsed_count = 0
    heads_present = 0
    if collapse_versions:
        edges = _load_relation_edges(eng)
        if edges:
            relevant, collapsed_rel = _vc.collapse_to_heads(relevant, edges)
            query_knowledge, collapsed_q = _vc.collapse_to_heads(query_knowledge, edges)
            collapsed_count = len(collapsed_rel) + len(collapsed_q)
            # Render-only surfacing: how many *surviving* items are the current
            # HEAD of a version chain (so the owner sees "this is the latest").
            heads = _vc.head_ids(edges)
            heads_present = sum(
                1 for item in (relevant + query_knowledge)
                if isinstance(item, dict) and item.get("id") in heads
            )

    governance = None
    if hasattr(eng, "root"):
        governance = {"trust_level": "private-self"}  # CLI runs as the owner

    payload = _recall.build_recall_payload(
        identity=identity,
        recent_activity=recent_activity,
        relevant_knowledge=relevant,
        query_knowledge=query_knowledge,
        project=project_folder,
        query=query,
        token_budget=token_budget,
        include_freshness=include_freshness,
        governance=governance,
        now=now,
    )
    payload["meta"]["collapsed_versions"] = collapsed_count
    payload["meta"]["version_chain"] = {
        "collapsed": collapsed_count,
        "heads_present": heads_present,
    }
    return payload


def _load_relation_edges(eng: Any) -> list[dict]:
    """Best-effort load of typed relation edges for version collapse.

    Uses ``RelationStore`` under the engram root when available; returns an
    empty list (collapse becomes a no-op) on any problem.
    """
    root = getattr(eng, "root", None)
    if root is None:
        return []
    try:
        from .governance_store import RelationStore

        return RelationStore(root).all_edges()
    except Exception:  # pragma: no cover - defensive
        return []


def render_recall_text(payload: dict[str, Any]) -> str:
    """Render a recall payload as a compact, owner-facing text digest."""
    lines: list[str] = []
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    project = meta.get("project") or "(all projects)"
    lines.append(f"Recall digest — project: {project}")
    if meta.get("query"):
        lines.append(f"  query focus: {meta['query']}")

    identity = payload.get("identity", {})
    if identity:
        lines.append("Identity:")
        for key in _IDENTITY_FIELDS:
            if key in identity:
                lines.append(f"  {key}: {identity[key]}")
        for key in _IDENTITY_LIST_FIELDS:
            if key in identity:
                lines.append(f"  {key}: {', '.join(identity[key])}")

    activity = payload.get("recent_activity", {})
    if activity:
        bits = [f"{k}={v}" for k, v in activity.items()]
        lines.append("Recent activity: " + ", ".join(bits))

    knowledge = payload.get("knowledge", [])
    lines.append(f"Knowledge ({len(knowledge)} items):")
    for item in knowledge:
        label = item.get("summary") or item.get("choice") or item.get("question") or "(no summary)"
        prefix = item.get("type", "item")
        fresh = ""
        fr = item.get("freshness")
        if isinstance(fr, dict) and fr.get("freshness_status"):
            fresh = f" [{fr['freshness_status']}]"
        # Surface provenance (source agent) by default so the owner can see where
        # each item came from — same data already in the payload, just rendered.
        prov = ""
        pv = item.get("provenance")
        if isinstance(pv, dict) and pv.get("source_agent"):
            prov = f" «src:{pv['source_agent']}»"
        lines.append(f"  - ({prefix}){fresh}{prov} {label}")

    gov = meta.get("governance", {})
    excluded = gov.get("excluded_count", 0) if isinstance(gov, dict) else 0
    collapsed = meta.get("collapsed_versions", 0)
    vcmeta = meta.get("version_chain", {}) if isinstance(meta, dict) else {}
    heads_present = vcmeta.get("heads_present", 0) if isinstance(vcmeta, dict) else 0
    footer = (
        f"  (trimmed by budget: {excluded}; older versions hidden: {collapsed}; "
        f"current versions/HEAD surfaced: {heads_present})"
    )
    lines.append(footer)
    return "\n".join(lines)
