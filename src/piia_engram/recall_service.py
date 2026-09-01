"""Recall service (Phase 6) — gather + render around the pure aggregator.

:mod:`recall.build_recall_payload` is a *pure* assembler that takes already
loaded sub-results. This module is the thin **owner-context gather layer** that
fetches those sub-results from a live :class:`Engram` through its existing,
already-governed read methods, optionally collapses superseded versions to HEAD
(:mod:`version_chain`), and renders a human-readable digest for the CLI.

MCP exposure:
    ``get_recall`` is now exposed as a thin MCP wrapper around this gather layer.
    Because the aggregate overlaps ``get_resume_brief`` and can combine identity,
    recent activity, and knowledge, the wrapper performs an owner-only governance
    preflight before calling this function. Non-owner callers are refused before
    any gather/search/telemetry side effect can run. The CLI (``engram recall``)
    continues to run as the owner (``private-self``) over the user's own store.

The gather layer is duck-typed against the Engram read API and defensive: a
missing/empty sub-result degrades to an empty slice rather than raising, so the
recall digest is always producible.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from . import governance_runtime as _gov_rt
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


def _flatten_kv_list(value: Any) -> list[str]:
    """Project a dict/list/str into the short-scalar list the slice expects."""
    if isinstance(value, dict):
        return [
            f"{key}: {val}" for key, val in value.items()
            if str(val).strip() and not str(key).startswith("_")
        ]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _augment_identity_profile(eng: Any, profile: dict[str, Any] | None) -> dict[str, Any]:
    """Fill the identity-slice list fields from their dedicated stores.

    ``_IDENTITY_LIST_FIELDS`` (preferences / quality_standards / work_patterns)
    were specified as profile fields, but live stores keep them in dedicated
    files (``preferences.json`` / ``quality_standards.json``). Without this
    merge the recall identity slice never carries them — a real injection gap
    surfaced by ``engram preview``. Profile-resident values win (no overwrite);
    fields named in ``trust_boundaries.restricted_fields`` are never merged.
    All reads are defensive: a missing or raising method merges nothing.
    """
    out = dict(profile) if isinstance(profile, dict) else {}

    restricted: set[str] = set()
    tb_getter = getattr(eng, "get_trust_boundaries", None)
    if callable(tb_getter):
        try:
            boundaries = tb_getter() or {}
            restricted = {
                str(field) for field in boundaries.get("restricted_fields", []) or []
            }
        except Exception:  # pragma: no cover - defensive; never block recall
            restricted = set()

    prefs: dict[str, Any] = {}
    prefs_getter = getattr(eng, "get_preferences", None)
    if callable(prefs_getter):
        try:
            prefs = prefs_getter() or {}
        except Exception:  # pragma: no cover - defensive
            prefs = {}
    if isinstance(prefs, dict):
        if "work_patterns" not in out and "work_patterns" not in restricted:
            patterns = _flatten_kv_list(prefs.get("work_patterns"))
            if patterns:
                out["work_patterns"] = patterns
        if "preferences" not in out and "preferences" not in restricted:
            pieces = _flatten_kv_list(prefs.get("tool_preferences"))
            communication = prefs.get("communication")
            if isinstance(communication, str) and communication.strip():
                pieces.append(f"communication: {communication.strip()}")
            if pieces:
                out["preferences"] = pieces

    if "quality_standards" not in out and "quality_standards" not in restricted:
        quality: dict[str, Any] = {}
        quality_getter = getattr(eng, "get_quality_standards", None)
        if callable(quality_getter):
            try:
                quality = quality_getter() or {}
            except Exception:  # pragma: no cover - defensive
                quality = {}
        if isinstance(quality, dict):
            rules = _flatten_kv_list(quality.get("rules"))
            if rules:
                out["quality_standards"] = rules

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
    include_trust: bool = False,
    collapse_versions: bool = True,
    include_playbooks: bool = False,
    role_scoped_memory: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble a recall payload from a live Engram instance.

    Calls only existing read methods (``get_safe_profile``/``get_profile``,
    ``get_recent_context``, ``get_relevant_lessons``, ``search_knowledge``),
    optionally collapses superseded knowledge to its current head using typed
    relation edges, then delegates to :func:`recall.build_recall_payload`.

    v4.20: ``include_playbooks`` (opt-in, default False) adds a playbook
    pointer bucket — recent verified+active project-visible playbooks via the
    existing zero-write ``get_recent_playbooks`` reader, plus query hits from
    the playbooks scope of ``search_knowledge`` (project_folder threaded).
    Playbook pointers are projected metadata-only and share the knowledge
    budget under a sub-cap inside ``build_recall_payload``.

    All sub-fetches are guarded: a method that is missing or raises yields an
    empty slice, so the digest is always producible. Returns the standard
    Recall Surface v1 payload plus a ``meta.collapsed_versions`` count.
    """
    sources = gather_recall_sources(
        eng,
        project_folder=project_folder,
        query=query,
        limit=limit,
        collapse_versions=collapse_versions,
        include_playbooks=include_playbooks,
    )
    identity = sources["identity"]
    recent_activity = sources["recent_activity"]
    relevant = sources["relevant"]
    query_knowledge = sources["query_knowledge"]
    playbooks = sources.get("playbooks") or []
    collapsed_count = sources["collapsed_count"]
    heads_present = sources["heads_present"]

    # --- optional role-scoped memory ------------------------------------
    role_scope_meta: dict[str, Any] = {"enabled": False}
    if (
        role_scoped_memory
        and getattr(eng, "root", None) is not None
        and _gov_rt.governance_enabled()
    ):
        before = len(relevant) + len(query_knowledge)
        buckets = _gov_rt.maybe_govern_buckets(
            eng.root,
            {"project_relevant": relevant, "query": query_knowledge},
            tool="get_recall",
            declared_task=query,
        )
        relevant = buckets.get("project_relevant", relevant)
        query_knowledge = buckets.get("query", query_knowledge)
        after = len(relevant) + len(query_knowledge)
        perms = _gov_rt.describe_caller_permissions(eng.root)
        role_scope_meta = {
            "enabled": _gov_rt.governance_enabled(),
            "filtered": max(0, before - after),
            "max_sensitivity": perms.get("max_sensitivity"),
            "staging_excluded": (
                perms.get("permission_profile_vnext", {}).get("staging_excluded")
                if isinstance(perms.get("permission_profile_vnext"), dict)
                else False
            ),
        }

    governance = None
    if hasattr(eng, "root"):
        governance = {"trust_level": "private-self"}  # CLI runs as the owner

    payload = _recall.build_recall_payload(
        identity=identity,
        recent_activity=recent_activity,
        relevant_knowledge=relevant,
        query_knowledge=query_knowledge,
        playbooks=playbooks,
        project=project_folder,
        query=query,
        token_budget=token_budget,
        include_freshness=include_freshness,
        include_trust=include_trust,
        governance=governance,
        now=now,
    )
    payload["meta"]["collapsed_versions"] = collapsed_count
    payload["meta"]["version_chain"] = {
        "collapsed": collapsed_count,
        "heads_present": heads_present,
    }
    usage = payload["meta"].setdefault("context_usage", {})
    if isinstance(usage, dict):
        usage["version_chain"] = {
            "collapsed": collapsed_count,
            "heads_present": heads_present,
        }
        usage["role_scope"] = role_scope_meta
    return payload


def gather_recall_sources(
    eng: Any,
    *,
    project_folder: str = "",
    query: str = "",
    limit: int = 8,
    collapse_versions: bool = True,
    include_playbooks: bool = False,
) -> dict[str, Any]:
    """Fetch phase of recall: identity slice, recent activity, raw knowledge.

    This is the single read path shared by :func:`gather_recall` (which
    projects + budgets the result into the Recall Surface v1 payload) and
    :mod:`context_preview` (which needs the *raw* items so per-item
    ``sensitivity``/``tier`` survive for the exposed/withheld split).

    v4.20: ``include_playbooks`` adds a raw ``playbooks`` bucket (recent
    verified+active project-visible via the zero-write recent reader, plus
    query hits from the playbooks scope with ``project_folder`` threaded);
    superseded snapshots never enter (status/tier filtered at the source).

    All sub-fetches are guarded exactly like ``gather_recall``: a missing or
    raising method yields an empty slice. Returns a dict with ``identity``,
    ``recent_activity``, ``relevant``, ``query_knowledge`` (both raw,
    post-version-collapse), ``playbooks`` (opt-in), ``collapsed_count`` and
    ``heads_present``.
    """
    # --- identity -------------------------------------------------------
    profile: dict[str, Any] | None = None
    getter = getattr(eng, "get_safe_profile", None) or getattr(eng, "get_profile", None)
    if callable(getter):
        try:
            profile = getter()
        except Exception:  # pragma: no cover - defensive; never block recall
            profile = None
    identity = _identity_slice(_augment_identity_profile(eng, profile))

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
    # NOTE: the query search itself deliberately keeps its historical shape
    # (no project_folder) — threading it would change lesson/decision recall
    # semantics, which is out of the v4.20 scope. Project-scoping for the
    # playbook bucket is applied as a post-filter below.
    query_knowledge: list[dict[str, Any]] = []
    playbook_query_hits: list[dict[str, Any]] = []
    if query and hasattr(eng, "search_knowledge"):
        try:
            hits = eng.search_knowledge(query, scope="all", limit=limit) or {}
        except Exception:  # pragma: no cover - defensive
            hits = {}
        for bucket in ("lessons", "decisions"):
            rows = hits.get(bucket) if isinstance(hits, dict) else None
            if isinstance(rows, list):
                query_knowledge.extend(r for r in rows if isinstance(r, dict))
        rows = hits.get("playbooks") if isinstance(hits, dict) else None
        if isinstance(rows, list):
            playbook_query_hits.extend(r for r in rows if isinstance(r, dict))

    # --- playbook bucket (v4.20, opt-in) ----------------------------------
    playbooks: list[dict[str, Any]] = []
    if include_playbooks:
        recent_pbs: list[dict[str, Any]] = []
        if hasattr(eng, "get_recent_playbooks"):
            try:
                recent_pbs = eng.get_recent_playbooks(
                    limit=4, project_folder=project_folder or None
                ) or []
            except Exception:  # pragma: no cover - defensive
                recent_pbs = []
        # project-scoped query hits: keep only playbooks visible for THIS
        # project (same visibility predicate the recent reader applies)
        visible_query_pbs: list[dict[str, Any]] = []
        for pb in playbook_query_hits:
            visible = getattr(eng, "_playbook_visible_for_project", None)
            if callable(visible):
                try:
                    if visible(pb, project_folder or None):
                        visible_query_pbs.append(pb)
                except Exception:  # pragma: no cover - defensive
                    continue
            else:
                visible_query_pbs.append(pb)
        # active+verified enforced defensively even if a reader loosens later
        playbooks = [
            pb
            for pb in (recent_pbs + visible_query_pbs)
            if isinstance(pb, dict)
            and pb.get("status") == "active"
            and pb.get("tier") == "verified"
            and pb.get("snapshot_of") is None
        ]
        # de-dup by id (recent reader and query scope can both return one)
        seen_pb: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for pb in playbooks:
            key = str(pb.get("id") or id(pb))
            if key in seen_pb:
                continue
            seen_pb.add(key)
            deduped.append(pb)
        playbooks = deduped

    # --- version collapse (prefer HEAD) ---------------------------------
    collapsed_count = 0
    heads_present = 0
    if collapse_versions:
        edges = _load_relation_edges(eng)
        if edges:
            relevant, collapsed_rel = _vc.collapse_to_heads(relevant, edges)
            query_knowledge, collapsed_q = _vc.collapse_to_heads(query_knowledge, edges)
            playbooks, collapsed_pb = _vc.collapse_to_heads(playbooks, edges)
            collapsed_count = len(collapsed_rel) + len(collapsed_q) + len(collapsed_pb)
            # Render-only surfacing: how many *surviving* items are the current
            # HEAD of a version chain (so the owner sees "this is the latest").
            heads = _vc.head_ids(edges)
            heads_present = sum(
                1 for item in (relevant + query_knowledge + playbooks)
                if isinstance(item, dict) and item.get("id") in heads
            )

    return {
        "identity": identity,
        "recent_activity": recent_activity,
        "relevant": relevant,
        "query_knowledge": query_knowledge,
        "playbooks": playbooks,
        "collapsed_count": collapsed_count,
        "heads_present": heads_present,
    }


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
    # [Engram] presence header — brand the recall block. Counts come ONLY from
    # freshness_status; the recall payload projects no tier, so we never imply a
    # "verified" count we cannot substantiate. Degrades to a bare memory count
    # when no item carries freshness (include_freshness=False) or knowledge=[].
    _knowledge = payload.get("knowledge", []) if isinstance(payload, dict) else []

    def _fresh_status(_it: object) -> str:
        if isinstance(_it, dict) and isinstance(_it.get("freshness"), dict):
            return str(_it["freshness"].get("freshness_status") or "")
        return ""

    if any(_fresh_status(_it) for _it in _knowledge):
        _fresh_n = sum(1 for _it in _knowledge if _fresh_status(_it) == "fresh")
        _stale_n = sum(1 for _it in _knowledge if _fresh_status(_it) == "stale")
        lines.append(
            f"[Engram Recall] {len(_knowledge)} memories · "
            f"{_fresh_n} fresh · {_stale_n} stale"
        )
    else:
        lines.append(f"[Engram Recall] {len(_knowledge)} memories")
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
        labeling_tag = ""
        lb = item.get("labeling")
        if isinstance(lb, dict):
            validation = str(lb.get("validation_state") or "").strip()
            quality = str(lb.get("annotation_quality") or "").strip()
            if validation and quality:
                labeling_tag = f" [{validation}/{quality}]"
        # Surface provenance (source agent) by default so the owner can see where
        # each item came from — same data already in the payload, just rendered.
        prov = ""
        pv = item.get("provenance")
        if isinstance(pv, dict) and pv.get("source_agent"):
            prov = f" «src:{pv['source_agent']}»"
        lines.append(f"  - ({prefix}){fresh}{labeling_tag}{prov} {label}")
        # Owner trust block (owner/private-self only; gated upstream by
        # include_trust). Surfaces the 4.5.0 first-value payoff — why a fact is
        # trustworthy, its anchor, when it was validated, and when it expires.
        trust = item.get("trust")
        if isinstance(trust, dict):
            bits: list[str] = []
            if trust.get("confirmation_source"):
                bits.append(f"why={trust['confirmation_source']}")
            if trust.get("anchor"):
                bits.append(f"anchor={trust['anchor']}")
            if trust.get("anchor_status"):
                bits.append(f"status={trust['anchor_status']}")
            if trust.get("validated_at"):
                bits.append(f"validated={trust['validated_at']}")
            expires = trust.get("decay_policy") or trust.get("freshness_status")
            if expires:
                bits.append(f"expires={expires}")
            if bits:
                lines.append("      trust: " + ", ".join(bits))

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
    usage = meta.get("context_usage", {}) if isinstance(meta, dict) else {}
    if isinstance(usage, dict):
        knowledge_usage = usage.get("knowledge", {})
        budget_usage = usage.get("budget", {})
        if isinstance(knowledge_usage, dict) and isinstance(budget_usage, dict):
            returned = knowledge_usage.get("returned", len(knowledge))
            trimmed = knowledge_usage.get("trimmed_by_budget", excluded)
            used = budget_usage.get("estimated_used_tokens", 0)
            requested = budget_usage.get("requested_tokens", meta.get("token_budget", 0))
            lines.append(
                "  context usage: "
                f"returned={returned}, trimmed={trimmed}, budget={used}/{requested}"
            )
    return "\n".join(lines)


def record_recall_funnel(
    payload: dict, *, current_tool: str = "", surface: str = "mcp"
) -> None:
    """Record the back-half first-value funnel events (trust + cross-tool payoff).

    Content-blind result extractor: derives bucketed outcomes from the recall
    payload only. The cross-tool relation is computed LOCALLY (the calling tool
    vs each item's source agent); only the relation + counts buckets are ever
    recorded — never the tool pair, item ids, or content. Gated by
    first_value_enabled() and wrapped so telemetry can never break recall.
    """
    from . import telemetry as _tel

    if not _tel.first_value_enabled():
        return
    try:
        items = payload.get("knowledge", []) if isinstance(payload, dict) else []
        items = [i for i in items if isinstance(i, dict)]
        rec_surface = surface if surface in _tel._FV_SURFACES else "unknown"

        # --- trust payoff: did the owner see why facts are trustworthy? ---
        trust_items = [i for i in items if isinstance(i.get("trust"), dict)]
        if trust_items:
            bases: set[str] = set()
            statuses: set[str] = set()
            has_validated = has_expiry = False
            for i in trust_items:
                t = i["trust"]
                if t.get("confirmation_source"):
                    bases.add(str(t["confirmation_source"]))
                if t.get("anchor_status"):
                    statuses.add(str(t["anchor_status"]))
                if t.get("validated_at"):
                    has_validated = True
                if t.get("decay_policy") or t.get("freshness_status"):
                    has_expiry = True
            basis = "mixed" if len(bases) > 1 else (next(iter(bases)) if bases else "unknown")
            if basis not in {"anchor", "human", "test_signal", "mixed", "unknown"}:
                basis = "unknown"
            if not statuses:
                amix = "none"
            elif statuses == {"valid"}:
                amix = "valid_only"
            elif statuses == {"unknown"}:
                amix = "unknown_only"
            else:
                amix = "mixed"
            _tel.record_first_value_event(
                "recall.trust.payoff",
                {
                    "payoff": True,
                    "trusted_items_bucket": _tel.bucket_recall(len(trust_items)),
                    "trust_basis": basis,
                    "anchor_status_mix": amix,
                    "has_validated_at": has_validated,
                    "has_expiry_signal": has_expiry,
                },
                surface=rec_surface,
            )

        # --- cross-tool payoff: recalled in a tool other than the one that wrote it
        ct = current_tool if current_tool in _tel._FV_TOOLS else "unknown"
        cross = trusted_cross = same = 0
        for i in items:
            origin = str((i.get("provenance") or {}).get("source_agent") or "")
            if not origin or ct == "unknown":
                continue
            if origin != ct:
                cross += 1
                if isinstance(i.get("trust"), dict):
                    trusted_cross += 1
            else:
                same += 1
        if cross and same:
            relation = "mixed"
        elif cross:
            relation = "cross_tool"
        elif same:
            relation = "same_tool"
        else:
            relation = "unknown"
        _tel.record_first_value_event(
            "recall.cross_tool.payoff",
            {
                "payoff": cross > 0,
                "current_tool": ct,
                "source_relation": relation,
                "cross_tool_items_bucket": _tel.bucket_recall(cross),
                "trusted_cross_tool_items_bucket": _tel.bucket_recall(trusted_cross),
                "recall_surface": "get_recall",
            },
            surface=rec_surface,
        )
    except Exception:
        pass
