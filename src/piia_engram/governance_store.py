"""Persistence for the governance layer — grants + typed relations.

File-backed, atomic (reuses storage._read_json/_write_json: lock + atomic
replace). Pure storage; does NOT touch the live read path. Feeds:
- ``governance.gate`` (trust level + revocation per agent)
- ``decision_thread.build_thread`` (typed edges)

Layout under the engram root:
- ``governance/grants.json``   — {"grants": {agent_id: level}, "revoked": [...]}
- ``knowledge/relations.json`` — [ {src, rel, dst}, ... ]
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import governance as gov
from .decision_thread import RELATION_TYPES, validate_edges
from .storage import _now_iso, _read_json, _update_json


def _normalize_grants(data) -> dict:
    if not isinstance(data, dict):
        return {"grants": {}, "revoked": []}
    grants = data.get("grants")
    revoked = data.get("revoked")
    return {
        "grants": grants if isinstance(grants, dict) else {},
        "revoked": revoked if isinstance(revoked, list) else [],
    }


class GrantStore:
    """Per-agent trust grants + revocations (the 'who may read what' record)."""

    def __init__(self, root: str | Path):
        self.path = Path(root) / "governance" / "grants.json"

    def _load(self) -> dict:
        return _normalize_grants(_read_json(self.path))

    def set_grant(self, agent_id: str, trust_level: str) -> None:
        """Bind an agent to a trust level. Granting clears any revocation.

        Atomic (read-modify-write under one lock) so concurrent grants from
        multiple tools don't lose each other."""
        if trust_level not in gov.TRUST_LEVELS:
            raise ValueError(
                f"unknown trust level {trust_level!r}; valid: {list(gov.TRUST_LEVELS)}"
            )
        agent_id = str(agent_id)

        def _mut(cur):
            data = _normalize_grants(cur)
            data["grants"][agent_id] = trust_level
            data["revoked"] = [a for a in data["revoked"] if a != agent_id]
            return data

        _update_json(self.path, _mut, default={"grants": {}, "revoked": []})

    def revoke(self, agent_id: str) -> None:
        """Revoke an agent (forward-only: stops future disclosure). Atomic."""
        agent_id = str(agent_id)

        def _mut(cur):
            data = _normalize_grants(cur)
            if agent_id not in data["revoked"]:
                data["revoked"].append(agent_id)
            return data

        _update_json(self.path, _mut, default={"grants": {}, "revoked": []})

    def is_revoked(self, agent_id: str) -> bool:
        return str(agent_id) in self._load()["revoked"]

    def trust_level_for(self, agent_id: str, client_type: str | None = None) -> str:
        """Explicit grant (by agent_id) wins; otherwise auto-classify by
        client_type (fail-closed for unknown — see governance.classify_agent)."""
        data = self._load()
        explicit = data["grants"].get(str(agent_id))
        if explicit in gov.TRUST_LEVELS:
            return explicit
        return gov.classify_agent(client_type if client_type is not None else agent_id)

    def list_grants(self) -> dict:
        data = self._load()
        return {"grants": dict(data["grants"]), "revoked": list(data["revoked"])}


class RelationStore:
    """Typed, directed relations between knowledge ids (decision threads)."""

    def __init__(self, root: str | Path):
        self.path = Path(root) / "knowledge" / "relations.json"

    def _load(self) -> list[dict]:
        data = _read_json(self.path)
        return data if isinstance(data, list) else []

    def add_relation(self, src: str, rel: str, dst: str) -> bool:
        """Add one typed edge atomically. Returns True if added, False if
        invalid or already present (idempotent)."""
        cleaned = validate_edges([{"src": src, "rel": rel, "dst": dst}])
        if not cleaned:
            return False
        edge = cleaned[0]
        outcome = {"added": False}

        def _mut(cur):
            data = cur if isinstance(cur, list) else []
            if edge in data:
                return data
            outcome["added"] = True
            return data + [edge]

        _update_json(self.path, _mut, default=[])
        return outcome["added"]

    def remove_relation(self, src: str, rel: str, dst: str) -> bool:
        edge = {"src": str(src), "rel": str(rel), "dst": str(dst)}
        outcome = {"removed": False}

        def _mut(cur):
            data = cur if isinstance(cur, list) else []
            kept = [e for e in data if e != edge]
            outcome["removed"] = len(kept) != len(data)
            return kept

        _update_json(self.path, _mut, default=[])
        return outcome["removed"]

    def all_edges(self) -> list[dict]:
        return validate_edges(self._load())

    def edges_for(self, node_id: str) -> list[dict]:
        nid = str(node_id)
        return [e for e in self.all_edges() if e["src"] == nid or e["dst"] == nid]


def conflict_pair_key(id1: str, id2: str) -> str:
    """Stable sorted key for a decision-conflict pair."""
    a, b = sorted((str(id1), str(id2)))
    return f"{a}::{b}"


def decision_conflict_fingerprint(decision: dict) -> str:
    """Short content fingerprint for dismissal drift hints."""
    text = f"{decision.get('question', '')}\n{decision.get('choice', '')}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _normalize_resolutions(data) -> dict[str, dict]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for value in data.values():
        if not isinstance(value, dict):
            continue
        id1 = value.get("id1")
        id2 = value.get("id2")
        if not id1 or not id2:
            continue
        key = conflict_pair_key(str(id1), str(id2))
        record = dict(value)
        record["pair_key"] = key
        out[key] = record
    return out


class ResolutionStore:
    """Persistent decision-conflict resolution records."""

    def __init__(self, root: str | Path):
        self.path = Path(root) / "knowledge" / "conflict_resolutions.json"

    def all_records(self) -> dict[str, dict]:
        return _normalize_resolutions(_read_json(self.path))

    def record(
        self,
        first: dict,
        second: dict,
        *,
        action: str,
        keep: str | None = None,
        note: str = "",
        resolved_at: str | None = None,
    ) -> dict:
        id1 = str(first.get("id") or "")
        id2 = str(second.get("id") or "")
        if not id1 or not id2:
            raise ValueError("decision conflict resolution requires two ids")
        key = conflict_pair_key(id1, id2)
        record = {
            "pair_key": key,
            "id1": id1,
            "id2": id2,
            "action": str(action),
            "keep": str(keep or ""),
            "resolved_at": resolved_at or _now_iso(),
            "id1_hash": decision_conflict_fingerprint(first),
            "id2_hash": decision_conflict_fingerprint(second),
            "note": str(note or ""),
        }
        result = {"record": record}

        def _mut(cur):
            data = _normalize_resolutions(cur)
            existing = data.get(key)
            if existing and all(
                existing.get(field) == record.get(field)
                for field in ("id1", "id2", "action", "keep", "id1_hash", "id2_hash", "note")
            ):
                result["record"] = existing
                return data
            data[key] = record
            return data

        _update_json(self.path, _mut, default={})
        return result["record"]

    def dismiss(
        self,
        first: dict,
        second: dict,
        *,
        note: str = "",
        resolved_at: str | None = None,
    ) -> dict:
        return self.record(
            first,
            second,
            action="dismiss",
            note=note,
            resolved_at=resolved_at,
        )

    def is_suppressed(self, first: dict, second: dict) -> bool:
        id1 = first.get("id")
        id2 = second.get("id")
        if not id1 or not id2:
            return False
        return conflict_pair_key(str(id1), str(id2)) in self.all_records()

    def content_changed(self, first: dict, second: dict) -> bool:
        id1 = str(first.get("id") or "")
        id2 = str(second.get("id") or "")
        if not id1 or not id2:
            return False
        record = self.all_records().get(conflict_pair_key(id1, id2))
        if not record:
            return False
        current = {
            id1: decision_conflict_fingerprint(first),
            id2: decision_conflict_fingerprint(second),
        }
        stored = {
            str(record.get("id1", "")): record.get("id1_hash", ""),
            str(record.get("id2", "")): record.get("id2_hash", ""),
        }
        return any(stored.get(decision_id) != fp for decision_id, fp in current.items())

    def replace_all(self, records: dict | None) -> None:
        data = _normalize_resolutions(records or {})
        _update_json(self.path, lambda _cur: data, default={})

    def merge_records(self, records: dict | None) -> int:
        incoming = _normalize_resolutions(records or {})
        changed = {"count": 0}

        def _mut(cur):
            data = _normalize_resolutions(cur)
            for key, record in incoming.items():
                existing = data.get(key)
                if (
                    existing is None
                    or str(record.get("resolved_at", "")) >= str(existing.get("resolved_at", ""))
                ):
                    if existing != record:
                        changed["count"] += 1
                    data[key] = record
            return data

        _update_json(self.path, _mut, default={})
        return changed["count"]
