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

from pathlib import Path

from . import governance as gov
from .decision_thread import RELATION_TYPES, validate_edges
from .storage import _read_json, _write_json


class GrantStore:
    """Per-agent trust grants + revocations (the 'who may read what' record)."""

    def __init__(self, root: str | Path):
        self.path = Path(root) / "governance" / "grants.json"

    def _load(self) -> dict:
        data = _read_json(self.path)
        if not isinstance(data, dict):
            return {"grants": {}, "revoked": []}
        data.setdefault("grants", {})
        data.setdefault("revoked", [])
        if not isinstance(data["grants"], dict):
            data["grants"] = {}
        if not isinstance(data["revoked"], list):
            data["revoked"] = []
        return data

    def set_grant(self, agent_id: str, trust_level: str) -> None:
        """Bind an agent to a trust level. Granting clears any revocation."""
        if trust_level not in gov.TRUST_LEVELS:
            raise ValueError(
                f"unknown trust level {trust_level!r}; valid: {list(gov.TRUST_LEVELS)}"
            )
        agent_id = str(agent_id)
        data = self._load()
        data["grants"][agent_id] = trust_level
        data["revoked"] = [a for a in data["revoked"] if a != agent_id]
        _write_json(self.path, data)

    def revoke(self, agent_id: str) -> None:
        """Revoke an agent (forward-only: stops future disclosure)."""
        agent_id = str(agent_id)
        data = self._load()
        if agent_id not in data["revoked"]:
            data["revoked"].append(agent_id)
        _write_json(self.path, data)

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
        """Add one typed edge. Returns True if added, False if invalid or
        already present (idempotent)."""
        cleaned = validate_edges([{"src": src, "rel": rel, "dst": dst}])
        if not cleaned:
            return False
        edge = cleaned[0]
        data = self._load()
        if edge in data:
            return False
        data.append(edge)
        _write_json(self.path, data)
        return True

    def remove_relation(self, src: str, rel: str, dst: str) -> bool:
        edge = {"src": str(src), "rel": str(rel), "dst": str(dst)}
        data = self._load()
        kept = [e for e in data if e != edge]
        if len(kept) == len(data):
            return False
        _write_json(self.path, kept)
        return True

    def all_edges(self) -> list[dict]:
        return validate_edges(self._load())

    def edges_for(self, node_id: str) -> list[dict]:
        nid = str(node_id)
        return [e for e in self.all_edges() if e["src"] == nid or e["dst"] == nid]
