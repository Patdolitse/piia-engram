"""Decision conflict detection and rendering helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from .decision_thread import connected_component, validate_edges
from .governance_store import (
    conflict_pair_key,
    decision_conflict_fingerprint,
)
from .storage import CONFLICT_C_CEILING, CONFLICT_Q_THRESHOLD


def truncate_text(text: Any, limit: int = 80) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "..."


def _domains(entry: dict) -> set[str]:
    raw = entry.get("domain") or entry.get("project") or ""
    return {part.strip() for part in str(raw).split(",") if part.strip()}


def _identity_text(entry: dict) -> str:
    return str(entry.get("question") or entry.get("title") or entry.get("summary") or "")


def _resolution_for(resolutions: dict | list | None, id1: str, id2: str) -> dict | None:
    if not resolutions:
        return None
    key = conflict_pair_key(id1, id2)
    if isinstance(resolutions, dict):
        value = resolutions.get(key)
        return value if isinstance(value, dict) else None
    if isinstance(resolutions, list):
        for item in resolutions:
            if not isinstance(item, dict):
                continue
            a = item.get("id1")
            b = item.get("id2")
            if a and b and conflict_pair_key(str(a), str(b)) == key:
                return item
    return None


def _content_changed(record: dict, first: dict, second: dict) -> bool:
    current = {
        str(first.get("id", "")): decision_conflict_fingerprint(first),
        str(second.get("id", "")): decision_conflict_fingerprint(second),
    }
    stored = {
        str(record.get("id1", "")): record.get("id1_hash", ""),
        str(record.get("id2", "")): record.get("id2_hash", ""),
    }
    return any(stored.get(decision_id) != fp for decision_id, fp in current.items())


def _supersedes_components(relations: Iterable[dict] | None) -> dict[str, frozenset[str]]:
    edges = [
        edge for edge in validate_edges(relations or [])
        if edge.get("rel") == "supersedes"
    ]
    components: dict[str, frozenset[str]] = {}
    for edge in edges:
        for node in (edge["src"], edge["dst"]):
            if node not in components:
                components[node] = frozenset(connected_component(node, edges))
    return components


def _same_supersedes_component(
    id1: str,
    id2: str,
    components: dict[str, frozenset[str]],
) -> bool:
    comp = components.get(id1)
    return bool(comp and id2 in comp)


def detect_active_decision_conflicts(
    decisions: list[dict],
    relations: Iterable[dict] | None = None,
    resolutions: dict | list | None = None,
    *,
    similarity: Callable[[str, str], float],
    identity_text: Callable[[dict], str] | None = None,
    q_threshold: float = CONFLICT_Q_THRESHOLD,
    c_ceiling: float = CONFLICT_C_CEILING,
    include_suppressed: bool = False,
) -> list[dict]:
    """Return actionable active decision conflict pairs.

    ``similarity`` is injected so the retrieval layer keeps using its token-F1
    scorer. ``relations`` only suppresses pairs connected by ``supersedes``.
    """
    conflicts: list[dict] = []
    text_for = identity_text or _identity_text
    components = _supersedes_components(relations)

    active = [d for d in decisions if d.get("status", "active") == "active"]
    for i, first in enumerate(active):
        id1 = str(first.get("id") or "")
        if not id1:
            continue
        for second in active[i + 1:]:
            id2 = str(second.get("id") or "")
            if not id2:
                continue
            dom1 = _domains(first)
            dom2 = _domains(second)
            if dom1 and dom2 and not (dom1 & dom2):
                continue
            if _same_supersedes_component(id1, id2, components):
                continue

            q1 = text_for(first)
            q2 = text_for(second)
            q_sim = similarity(q1, q2)
            if q_sim < q_threshold:
                continue

            c1 = str(first.get("choice", ""))
            c2 = str(second.get("choice", ""))
            c_sim = similarity(c1, c2)
            if c_sim >= c_ceiling:
                continue

            record = _resolution_for(resolutions, id1, id2)
            suppressed = record is not None
            if suppressed and not include_suppressed:
                continue

            item = {
                "type": "decision",
                "id1": id1,
                "id2": id2,
                "q1": q1,
                "q2": q2,
                "c1": c1,
                "c2": c2,
                "q_sim": q_sim,
                "c_sim": c_sim,
            }
            if suppressed and record:
                item["suppressed"] = True
                item["resolution_action"] = record.get("action", "")
                item["content_changed"] = _content_changed(record, first, second)
            conflicts.append(item)
    return conflicts


def split_conflicts(conflicts: list[dict]) -> tuple[list[dict], list[dict]]:
    unsuppressed = [c for c in conflicts if not c.get("suppressed")]
    suppressed = [c for c in conflicts if c.get("suppressed")]
    return unsuppressed, suppressed


def sample_conflicts(conflicts: list[dict], limit: int = 10) -> list[dict]:
    samples: list[dict] = []
    for conflict in conflicts[:limit]:
        samples.append({
            "id1": conflict.get("id1", ""),
            "id2": conflict.get("id2", ""),
            "q_sim": round(float(conflict.get("q_sim", 0.0)), 3),
            "c_sim": round(float(conflict.get("c_sim", 0.0)), 3),
            "q1": truncate_text(conflict.get("q1", "")),
            "q2": truncate_text(conflict.get("q2", "")),
            "content_changed": bool(conflict.get("content_changed", False)),
        })
    return samples
