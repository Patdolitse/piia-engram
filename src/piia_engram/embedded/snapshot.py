"""Bounded, zero-write task-context retrieval for an embedding host.

Semantics are isomorphic with the consumer contract
``core.engram_task_context_snapshot.v1``: same field set, same counting rules,
same ``context_empty`` status ladder, and the same canonical hash discipline, so
a host can validate a facade snapshot with the validator it already has once it
accepts this schema name.

Only the ``schema`` string differs, deliberately: this runtime does not emit
identifiers in a consumer's namespace. Hosts admit the facade through the
handshake (facade contract + snapshot schema), not through a version string.

Phase 1 is read-only. The store is opened with ``read_only=True`` and the
persistent hybrid index is never touched, so a retrieval performs no store
write, no index materialisation, no network and no subprocess.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .contract import SNAPSHOT_SCHEMA, canonical_hash

MAX_ITEMS = 8
MAX_SUMMARY = 240
SEARCH_MODE = "keyword_no_persistent_index"
SHARING_BASIS = "explicit_source_public_equivalent_fields"

SNAPSHOT_FIELDS = {
    "schema", "retrieval_query", "scope", "scope_hash", "previous_context_hash",
    "source_hashes", "included_count", "matched_count", "withheld_count",
    "provider_included_count", "excluded_count", "excluded_by_reason", "items",
    "status", "engram_read_only", "engram_write_performed", "provider_authority",
    "context_hash",
}
ITEM_FIELDS = {
    "source_kind", "trust", "applicability", "sharing_class", "sharing_basis",
    "public_equivalent_summary", "counterevidence", "stop_conditions", "source_hash",
}
SOURCE_KINDS = {"project_context", "lesson", "decision", "playbook"}
TRUST = {"verified", "project_bound"}
TASK_CLASSES = {
    "software_development", "architecture_review", "maintenance", "verification",
    "research", "analysis", "professional_task",
}

_PATH = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|(?:^|\s)/(?:home|users|root|tmp|var|etc)/)")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+|\bsk-[a-z0-9._-]{6,}|authorization\s*[:=]|"
    r"(?:api|access|private|session)[_-]?(?:key|token|secret)\s*[:=])"
)
_FORBIDDEN = re.compile(
    r"(?i)(?:raw[_ -]?(?:chat|code|diff|log|prompt)|hidden[_ -]?(?:reasoning|chain)|"
    r"quarantine|candidate_unavailable)"
)


class FacadeContextError(ValueError):
    """Bounded, opaque-code error. Message text carries no store content or paths."""


def retrieve_task_context_snapshot(
    *,
    engram_root: Path | str,
    project_folder: Path | str,
    project_id: str,
    task_id: str,
    task_class: str,
    objective: str,
    limit: int = MAX_ITEMS,
    previous_context_hash: str | None = None,
) -> dict[str, Any]:
    """Perform one bounded read-only retrieval and return an immutable snapshot."""
    from ..core import Engram

    if type(limit) is not int or not 1 <= limit <= MAX_ITEMS:
        raise FacadeContextError("context_limit_invalid")
    if task_class not in TASK_CLASSES:
        raise FacadeContextError("context_task_class_invalid")
    if not isinstance(project_id, str) or not project_id.strip():
        raise FacadeContextError("context_project_id_invalid")
    if not isinstance(task_id, str) or not task_id.strip():
        raise FacadeContextError("context_task_id_invalid")
    if not isinstance(objective, str):
        raise FacadeContextError("context_objective_unsafe")
    if _PATH.search(objective) or _SECRET.search(objective) or _FORBIDDEN.search(objective):
        raise FacadeContextError("context_objective_unsafe")
    if previous_context_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", previous_context_hash):
        raise FacadeContextError("previous_context_hash_invalid")

    scope = {
        "project_id": project_id,
        "task_id": task_id,
        "task_class": task_class,
        "objective_hash": hashlib.sha256(objective.encode("utf-8")).hexdigest(),
    }
    scope_hash = canonical_hash(scope)
    query = " ".join(x for x in (project_id, task_id, task_class, objective) if x).strip()[:600]
    query_binding = {
        "query": query,
        "scope": scope,
        "tier": "verified",
        "limit": limit,
        "search_mode": SEARCH_MODE,
    }

    # A host names a project with its own logical id, but this store scopes
    # entries by a path-derived canonical id. Comparing a stored scope against
    # the host's namespace would exclude every project-bound item as a scope
    # mismatch, so resolve the folder to its canonical ids here — the facade is
    # the only layer that knows this derivation — and accept either namespace.
    from ..storage import _project_id_aliases

    accepted_project_ids = {project_id, *_project_id_aliases(str(project_folder))}

    engram = Engram(root=Path(engram_root), read_only=True)
    project = engram.get_project_snapshot(str(project_folder))
    result = engram.search_knowledge(
        query,
        scope="all",
        limit=limit,
        filters={"tier": "verified"},
        allow_hybrid_index=False,
        project_folder=str(project_folder),
    )
    if (
        not isinstance(project, dict)
        or not isinstance(result, dict)
        or set(result) != {"lessons", "decisions", "playbooks"}
        or any(not isinstance(result[key], list) for key in result)
    ):
        raise FacadeContextError("engram_read_result_contract_mismatch")

    candidates: list[tuple[str, dict[str, Any]]] = []
    if project:
        candidates.append(("project_context", project))
    for kind in ("lessons", "decisions", "playbooks"):
        for item in result.get(kind, []):
            if isinstance(item, dict):
                candidates.append((kind[:-1], item))

    included: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    source_hashes: list[str] = []
    matched_count = 0
    withheld_count = 0

    for kind, item in candidates:
        source_hash = canonical_hash(item)
        source_hashes.append(source_hash)
        reason = _exclude_reason(
            kind, item,
            project_folder=str(project_folder),
            accepted_project_ids=accepted_project_ids,
            task_class=task_class,
        )
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        matched_count += 1
        summary = _public_summary(item)
        if not summary:
            # Matched locally but carries no separately authored public-equivalent
            # body: counted, never quoted.
            withheld_count += 1
            continue
        included.append({
            "source_kind": kind,
            "trust": "project_bound" if kind == "project_context" else "verified",
            "applicability": task_class,
            "sharing_class": "public_equivalent",
            "sharing_basis": SHARING_BASIS,
            "public_equivalent_summary": summary,
            "counterevidence": _safe_list(item.get("public_equivalent_counterevidence")),
            "stop_conditions": _safe_list(item.get("public_equivalent_stop_conditions")),
            "source_hash": source_hash,
        })
        if len(included) >= limit:
            break

    status = (
        "context_available" if included
        else "local_context_available_provider_content_withheld" if matched_count
        else "context_empty"
    )
    body = {
        "schema": SNAPSHOT_SCHEMA,
        "retrieval_query": query_binding,
        "scope": scope,
        "scope_hash": scope_hash,
        "previous_context_hash": previous_context_hash,
        "source_hashes": sorted(set(source_hashes)),
        "included_count": len(included),
        "matched_count": matched_count,
        "withheld_count": withheld_count,
        "provider_included_count": len(included),
        "excluded_count": sum(excluded.values()),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "items": included,
        "status": status,
        "engram_read_only": True,
        "engram_write_performed": False,
        "provider_authority": False,
        "context_hash": "",
    }
    body["context_hash"] = canonical_hash({k: v for k, v in body.items() if k != "context_hash"})
    validate_snapshot(body)
    return body


def validate_snapshot(value: dict[str, Any]) -> dict[str, Any]:
    """Validate shape, counting invariants and hash binding. Fail-closed."""
    if not isinstance(value, dict) or set(value) != SNAPSHOT_FIELDS or value.get("schema") != SNAPSHOT_SCHEMA:
        raise FacadeContextError("context_snapshot_schema_invalid")
    expected = canonical_hash({k: v for k, v in value.items() if k != "context_hash"})
    if (
        value.get("context_hash") != expected
        or value.get("engram_read_only") is not True
        or value.get("engram_write_performed") is not False
        or value.get("provider_authority") is not False
    ):
        raise FacadeContextError("context_snapshot_hash_invalid")

    scope = value.get("scope")
    query = value.get("retrieval_query")
    if (
        not isinstance(scope, dict)
        or set(scope) != {"project_id", "task_id", "task_class", "objective_hash"}
        or scope.get("task_class") not in TASK_CLASSES
        or not all(isinstance(scope.get(k), str) and scope[k] for k in ("project_id", "task_id"))
        or not re.fullmatch(r"[0-9a-f]{64}", str(scope.get("objective_hash")))
        or value.get("scope_hash") != canonical_hash(scope)
    ):
        raise FacadeContextError("context_snapshot_scope_invalid")
    if (
        not isinstance(query, dict)
        or set(query) != {"query", "scope", "tier", "limit", "search_mode"}
        or query.get("scope") != scope
        or query.get("tier") != "verified"
        or query.get("search_mode") != SEARCH_MODE
        or type(query.get("limit")) is not int
        or not 1 <= query["limit"] <= MAX_ITEMS
        or not isinstance(query.get("query"), str)
    ):
        raise FacadeContextError("context_snapshot_query_invalid")

    previous = value.get("previous_context_hash")
    if previous is not None and (not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous)):
        raise FacadeContextError("context_snapshot_previous_invalid")

    items = value.get("items")
    hashes = value.get("source_hashes")
    excluded = value.get("excluded_by_reason")
    if (
        not isinstance(items, list)
        or not isinstance(hashes, list)
        or hashes != sorted(hashes)
        or len(hashes) != len(set(hashes))
        or not all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{64}", x) for x in hashes)
    ):
        raise FacadeContextError("context_snapshot_sources_invalid")
    if not isinstance(excluded, dict) or not all(
        isinstance(k, str) and k and type(v) is int and v >= 0 for k, v in excluded.items()
    ):
        raise FacadeContextError("context_snapshot_counts_invalid")
    for item in items:
        _validate_item(item, hashes)

    counts = (
        value.get("included_count"), value.get("matched_count"), value.get("withheld_count"),
        value.get("provider_included_count"), value.get("excluded_count"),
    )
    if (
        any(type(x) is not int or x < 0 for x in counts)
        or value.get("included_count") != len(items)
        or value.get("provider_included_count") != len(items)
        or value.get("withheld_count", 0) + value.get("provider_included_count", 0) != value.get("matched_count")
        or value.get("excluded_count") != sum(excluded.values())
    ):
        raise FacadeContextError("context_snapshot_counts_invalid")

    expected_status = (
        "context_available" if items
        else "local_context_available_provider_content_withheld" if value["matched_count"]
        else "context_empty"
    )
    if value.get("status") != expected_status:
        raise FacadeContextError("context_snapshot_status_invalid")
    return value


def _validate_item(item: Any, source_hashes: list[str]) -> None:
    if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
        raise FacadeContextError("context_item_schema_invalid")
    if (
        item.get("source_kind") not in SOURCE_KINDS
        or item.get("trust") not in TRUST
        or item.get("applicability") not in TASK_CLASSES
        or item.get("sharing_class") != "public_equivalent"
        or item.get("sharing_basis") != SHARING_BASIS
    ):
        raise FacadeContextError("context_item_classification_invalid")
    if item.get("source_hash") not in source_hashes:
        raise FacadeContextError("context_item_source_binding_invalid")
    _validate_safe_text(item.get("public_equivalent_summary"), MAX_SUMMARY, "context_item_summary_invalid")
    for field in ("counterevidence", "stop_conditions"):
        values = item.get(field)
        if not isinstance(values, list) or len(values) > 3:
            raise FacadeContextError("context_item_bounds_invalid")
        for text in values:
            _validate_safe_text(text, 160, "context_item_bounds_invalid")


def _validate_safe_text(value: Any, limit: int, code: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > limit
        or any(ord(ch) < 32 for ch in value)
        or _PATH.search(value)
        or _SECRET.search(value)
        or _FORBIDDEN.search(value)
    ):
        raise FacadeContextError(code)


def _exclude_reason(
    kind: str,
    item: dict[str, Any],
    *,
    project_folder: str,
    accepted_project_ids: set[str],
    task_class: str,
) -> str | None:
    import json as _json

    status = str(item.get("status", "active")).lower()
    tier = str(item.get("tier", "verified")).lower()
    if status in {"staging", "pending", "rejected", "archived", "superseded"} or tier not in {"verified", "active"}:
        return "untrusted_or_inactive"
    text = _json.dumps(item, ensure_ascii=False, default=str)
    if _SECRET.search(text):
        return "credential_or_secret"
    if _FORBIDDEN.search(text):
        return "forbidden_private_material"
    item_project = item.get("project_folder") or item.get("source_project_folder")
    item_project_id = item.get("project_id")
    if kind != "project_context" and not item_project and not item_project_id:
        return "project_scope_unproven"
    if item_project and str(item_project).replace("\\", "/").rstrip("/").lower() != project_folder.replace("\\", "/").rstrip("/").lower():
        return "project_scope_mismatch"
    if item_project_id and str(item_project_id) not in accepted_project_ids:
        return "project_scope_mismatch"
    task_classes = item.get("task_classes")
    applicability = item.get("applicability_scope")
    if task_classes is None and isinstance(applicability, dict):
        task_classes = applicability.get("task_classes")
    if task_classes is not None and (not isinstance(task_classes, list) or task_class not in task_classes):
        return "task_class_mismatch"
    return None


def _public_summary(item: dict[str, Any]) -> str:
    """Only a separately authored, explicitly classified public-equivalent field
    may cross a provider boundary. Trust and project scope grant no sharing
    authority, and ordinary summary/title/notes stay private by default."""
    if item.get("sharing_class") != "public_equivalent":
        return ""
    raw = " ".join(str(item.get("public_equivalent_summary") or "").split())[:MAX_SUMMARY]
    if not raw or _PATH.search(raw) or _SECRET.search(raw) or _FORBIDDEN.search(raw):
        return ""
    return raw


def _safe_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value[:3]:
        text = " ".join(str(entry).split())[:160]
        if text and not (_PATH.search(text) or _SECRET.search(text) or _FORBIDDEN.search(text)):
            out.append(text)
    return out
