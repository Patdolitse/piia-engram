"""Metadata-only management projection for future GUI consumers."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from piia_engram.continuity_report import build_continuity_report


logger = logging.getLogger(__name__)
LOW_QUALITY_THRESHOLD = 0.70
REVIEW_ITEM_KEYS = frozenset(
    {
        "id",
        "kind",
        "tier",
        "status",
        "quality_score",
        "quality_status",
        "quality_signal_count",
        "quality_flag_count",
        "created_at",
    }
)
PLAYBOOK_ITEM_KEYS = frozenset(
    {
        "id",
        "state",
        "scope_type",
        "has_project_scope",
        "has_shared_scope",
        "project_count",
        "needs_scope_review",
        "version",
        "created_at",
        "last_updated",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _quality_score(item: dict[str, Any]) -> float | None:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict):
        return None
    score = extraction.get("quality_score")
    if isinstance(score, (int, float)):
        return float(score)
    return None


def _quality_status(score: float | None) -> str:
    if score is None:
        return "missing"
    return "low" if score < LOW_QUALITY_THRESHOLD else "ok"


def _quality_list_count(item: dict[str, Any], field: str) -> int:
    extraction = item.get("extraction")
    if not isinstance(extraction, dict):
        return 0
    values = extraction.get(field)
    return len(values) if isinstance(values, list) else 0


def _created_at(item: dict[str, Any]) -> str:
    return str(item.get("timestamp") or item.get("created_at") or "")


def _closed_entry(entry: dict[str, Any], expected_keys: frozenset[str]) -> dict[str, Any]:
    if set(entry) != expected_keys:
        missing = sorted(expected_keys - set(entry))
        extra = sorted(set(entry) - expected_keys)
        raise AssertionError(f"management view schema drift: missing={missing} extra={extra}")
    return entry


def _review_entry(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    score = _quality_score(item)
    return _closed_entry({
        "id": str(item.get("id") or ""),
        "kind": kind,
        "tier": str(item.get("tier") or ""),
        "status": str(item.get("status") or ""),
        "quality_score": score,
        "quality_status": _quality_status(score),
        "quality_signal_count": _quality_list_count(item, "quality_signals"),
        "quality_flag_count": _quality_list_count(item, "quality_flags"),
        "created_at": _created_at(item),
    }, REVIEW_ITEM_KEYS)


def _review_items(eng, limit: int) -> list[dict[str, Any]]:
    return _review_items_filtered(
        eng,
        limit=limit,
        review_kind="all",
        quality_status="all",
    )


def _review_items_filtered(
    eng,
    *,
    limit: int,
    review_kind: str,
    quality_status: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    kind_filter = str(review_kind or "all").strip().lower()
    quality_filter = str(quality_status or "all").strip().lower()
    for lesson in eng.get_lessons(limit=None, _update_access=False):
        if lesson.get("tier") == "staging":
            rows.append(_review_entry("lesson", lesson))
    for decision in eng.get_decisions(limit=None, _update_access=False):
        if decision.get("tier") == "staging":
            rows.append(_review_entry("decision", decision))
    if kind_filter in {"lesson", "decision"}:
        rows = [item for item in rows if item.get("kind") == kind_filter]
    if quality_filter in {"low", "ok", "missing"}:
        rows = [item for item in rows if item.get("quality_status") == quality_filter]
    rows.sort(key=lambda item: (item.get("created_at") or "", item.get("id") or ""), reverse=True)
    return rows[: max(0, int(limit))]


def _playbook_state(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "active").strip().lower()
    aliases = {"outdated": "archived", "archive": "archived"}
    return aliases.get(status, status or "active")


def _playbook_entry(item: dict[str, Any]) -> dict[str, Any]:
    scope = item.get("scope")
    if not isinstance(scope, dict):
        scope = {}
    scope_type = str(scope.get("type") or item.get("scope_type") or "global")
    if scope_type == "shared":
        project_count = len(scope.get("project_ids") or [])
    elif scope_type == "project":
        project_count = 1
    else:
        project_count = 0
    state = _playbook_state(item)
    return _closed_entry({
        "id": str(item.get("id") or ""),
        "state": state,
        "scope_type": scope_type,
        "has_project_scope": scope_type == "project",
        "has_shared_scope": scope_type == "shared",
        "project_count": project_count,
        "needs_scope_review": str(item.get("scope_review_status") or "") == "unresolved",
        "version": int(item.get("version") or 1),
        "created_at": str(item.get("created_at") or ""),
        "last_updated": str(item.get("last_updated") or item.get("last_reviewed") or ""),
    }, PLAYBOOK_ITEM_KEYS)


def _playbook_items(
    eng,
    *,
    project_folder: str,
    limit: int,
    playbook_state: str,
    scope_type: str,
) -> tuple[list[dict[str, Any]], int]:
    result = eng.list_playbooks_for_management(
        status="all",
        project_folder=project_folder or None,
        scope_type=scope_type,
        include_content=False,
        limit=max(0, int(limit)),
    )
    raw_items = result.get("items") if isinstance(result, dict) else []
    if not isinstance(raw_items, list):
        raw_items = []
    items = [_playbook_entry(item) for item in raw_items if isinstance(item, dict)]
    state_filter = str(playbook_state or "all").strip().lower()
    if state_filter in {"active", "archived", "deleted", "staging"}:
        items = [item for item in items if item.get("state") == state_filter]

    scope_review_pending = 0
    try:
        queue = eng.get_playbook_scope_review_queue(
            project_folders=[project_folder] if project_folder else None,
            limit=None,
        )
        scope_review_pending = int(queue.get("total") or 0)
    except Exception as exc:
        logger.debug("management view scope-review count unavailable: %s", exc)
        scope_review_pending = 0
    return items, scope_review_pending


def build_management_view(
    eng,
    *,
    project_folder: str = "",
    review_limit: int = 50,
    playbook_limit: int = 50,
    session_limit: int = 500,
    review_kind: str = "all",
    quality_status: str = "all",
    playbook_state: str = "all",
    scope_type: str = "all",
) -> dict[str, Any]:
    """Build a closed, metadata-only projection for management UIs."""
    review_kind = str(review_kind or "all").strip().lower()
    quality_status = str(quality_status or "all").strip().lower()
    playbook_state = str(playbook_state or "all").strip().lower()
    scope_type = str(scope_type or "all").strip().lower()
    if review_kind not in {"all", "lesson", "decision"}:
        review_kind = "all"
    if quality_status not in {"all", "low", "ok", "missing"}:
        quality_status = "all"
    if playbook_state not in {"all", "active", "archived", "deleted", "staging"}:
        playbook_state = "all"
    if scope_type not in {"all", "global", "project", "shared"}:
        scope_type = "all"

    reviews = _review_items_filtered(
        eng,
        limit=review_limit,
        review_kind=review_kind,
        quality_status=quality_status,
    )
    playbooks, scope_review_pending = _playbook_items(
        eng,
        project_folder=project_folder,
        limit=playbook_limit,
        playbook_state=playbook_state,
        scope_type=scope_type,
    )
    states = [str(item.get("state") or "") for item in playbooks]
    continuity = build_continuity_report(
        eng,
        project_folder=project_folder,
        session_limit=session_limit,
    )
    readiness_checks = continuity.get("readiness_checks")
    if not isinstance(readiness_checks, dict):
        readiness_checks = {}

    return {
        "schema": 1,
        "generated_at": _now_iso(),
        "filters": {
            "project": "set" if project_folder else "",
            "review_kind": review_kind,
            "quality_status": quality_status,
            "playbook_state": playbook_state,
            "scope_type": scope_type,
        },
        "storage": {
            "kind": "local_json",
            "root_configured": bool(getattr(eng, "root", None)),
            "network_egress": False,
        },
        "continuity": {
            "readiness_level": str(continuity.get("readiness_level") or "not_ready"),
            "readiness_checks": {
                str(key): bool(value) for key, value in sorted(readiness_checks.items())
            },
        },
        "review_queue": {
            "pending_count": len(reviews),
            "low_quality_count": sum(
                1 for item in reviews if item.get("quality_status") in {"low", "missing"}
            ),
            "items": reviews,
        },
        "playbooks": {
            "total": len(playbooks),
            "active_count": states.count("active"),
            "archived_count": states.count("archived"),
            "deleted_count": states.count("deleted"),
            "scope_review_pending_count": scope_review_pending,
            "items": playbooks,
        },
        "actions": {
            "review": ["approve", "archive"],
            "playbook": ["archive", "delete", "restore", "resolve_scope"],
        },
    }


READONLY_SURFACE_KEYS = frozenset(
    {
        "schema",
        "generated_at",
        "read_only",
        "capabilities",
        "view",
        "version_chains",
    }
)
CAPABILITY_KEYS = frozenset(
    {
        "read_only",
        "exposed_mutations",
        "advisory_actions",
        "network_listener",
        "transport",
    }
)
VERSION_CHAIN_KEYS = frozenset({"topic_count", "head_count", "superseded_count", "cycle_count"})


def _version_chain_panel(eng) -> dict[str, Any]:
    """Metadata-only version-chain summary for the GUI (counts only, no ids/bodies).

    Read-only: loads typed relation edges and derives per-chain totals via the
    pure :mod:`version_chain` report. Any failure degrades to zeros so the
    surface is always producible. Never reads or echoes knowledge content.
    """
    panel = {"topic_count": 0, "head_count": 0, "superseded_count": 0, "cycle_count": 0}
    root = getattr(eng, "root", None)
    if root is None:
        return panel
    try:
        from .governance_store import RelationStore
        from . import version_chain as _vc

        edges = RelationStore(root).all_edges()
        report = _vc.build_version_report(edges)
        totals = report.get("totals", {}) if isinstance(report, dict) else {}
        panel = {
            "topic_count": int(totals.get("topics", 0) or 0),
            "head_count": int(totals.get("heads", 0) or 0),
            "superseded_count": int(totals.get("superseded", 0) or 0),
            "cycle_count": int(totals.get("cycles", 0) or 0),
        }
    except Exception as exc:  # pragma: no cover - defensive; never block the surface
        logger.debug("version-chain panel unavailable: %s", exc)
    return panel


def build_readonly_management_surface(
    eng,
    *,
    project_folder: str = "",
    review_limit: int = 50,
    playbook_limit: int = 50,
    session_limit: int = 500,
    review_kind: str = "all",
    quality_status: str = "all",
    playbook_state: str = "all",
    scope_type: str = "all",
) -> dict[str, Any]:
    """Build a strictly read-only, GUI-ready management surface.

    Wraps :func:`build_management_view` (already closed-schema and metadata-only)
    with an explicit read-only capability contract and a metadata-only
    version-chain panel. The surface itself performs **no** writes, deletes, or
    applies and exposes no mutation callable — its ``advisory_actions`` are only
    labels a GUI could render; executing them stays the caller's separately
    governed concern. There is no server or network listener; the surface is a
    plain in-process return value. Use :func:`assert_readonly_surface_closed` to
    runtime-check the closed schema.
    """
    view = build_management_view(
        eng,
        project_folder=project_folder,
        review_limit=review_limit,
        playbook_limit=playbook_limit,
        session_limit=session_limit,
        review_kind=review_kind,
        quality_status=quality_status,
        playbook_state=playbook_state,
        scope_type=scope_type,
    )
    advisory = view.get("actions") if isinstance(view.get("actions"), dict) else {}
    return {
        "schema": 1,
        "generated_at": _now_iso(),
        "read_only": True,
        "capabilities": {
            "read_only": True,
            "exposed_mutations": [],
            "advisory_actions": advisory,
            "network_listener": False,
            "transport": "in_process_return_value",
        },
        "view": view,
        "version_chains": _version_chain_panel(eng),
    }


def assert_readonly_surface_closed(surface: dict[str, Any]) -> dict[str, Any]:
    """Validate the read-only surface against its closed schema (raises on drift).

    Checks the top-level keys, the capability contract (must declare read-only,
    no exposed mutations, no network listener), and the version-chain panel keys.
    Returns the surface unchanged so it can be used inline.
    """
    if set(surface) != set(READONLY_SURFACE_KEYS):
        missing = sorted(READONLY_SURFACE_KEYS - set(surface))
        extra = sorted(set(surface) - READONLY_SURFACE_KEYS)
        raise AssertionError(f"readonly surface schema drift: missing={missing} extra={extra}")

    caps = surface.get("capabilities")
    if not isinstance(caps, dict) or set(caps) != set(CAPABILITY_KEYS):
        raise AssertionError("readonly surface capability contract drift")
    if caps.get("read_only") is not True:
        raise AssertionError("readonly surface must declare read_only=True")
    if caps.get("exposed_mutations") != []:
        raise AssertionError("readonly surface must expose no mutations")
    if caps.get("network_listener") is not False:
        raise AssertionError("readonly surface must not declare a network listener")

    chains = surface.get("version_chains")
    if not isinstance(chains, dict) or set(chains) != set(VERSION_CHAIN_KEYS):
        raise AssertionError("readonly surface version-chain panel schema drift")
    return surface


def render_management_text(view: dict[str, Any]) -> str:
    """Render a compact metadata-only management summary."""
    review = view.get("review_queue") or {}
    playbooks = view.get("playbooks") or {}
    continuity = view.get("continuity") or {}
    lines = [
        "Engram management view",
        f"  Review queue: {review.get('pending_count', 0)} pending "
        f"({review.get('low_quality_count', 0)} low/missing quality)",
        f"  Playbooks: {playbooks.get('active_count', 0)} active, "
        f"{playbooks.get('archived_count', 0)} archived, "
        f"{playbooks.get('deleted_count', 0)} deleted",
        f"  Scope review: {playbooks.get('scope_review_pending_count', 0)} pending",
        f"  Continuity readiness: {continuity.get('readiness_level', 'not_ready')}",
    ]
    return "\n".join(lines) + "\n"
