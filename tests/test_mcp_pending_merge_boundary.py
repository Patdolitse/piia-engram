"""Pending proposals are merged only by the local reviewer."""

import asyncio
import hashlib
import json

import pytest

from piia_engram import mcp_server, write_provenance
from piia_engram.core import Engram


def _digest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("knowledge", "playbooks", "identity")
            for p in (root / folder).rglob("*") if p.is_file()}


@pytest.mark.parametrize("kind", ["lesson", "decision", "playbook"])
@pytest.mark.parametrize("pending_side", ["primary", "secondary"])
@pytest.mark.parametrize("route", ["mcp", "core"])
def test_pending_merge_refuses_before_any_write(tmp_path, monkeypatch, kind, pending_side, route):
    eng = Engram(root=tmp_path / "store")
    monkeypatch.setattr(mcp_server, "_engram", eng)
    monkeypatch.setattr(mcp_server, "_track", lambda *a, **k: None)
    if kind == "lesson":
        a = eng.add_lesson("Validate cache headers before enabling edge caching", tier="verified")
        b = eng.add_lesson("Measure queue lag before adding another consumer", tier="staging")
    elif kind == "decision":
        a = eng.add_decision({"question": "Which cache strategy?", "choice": "ttl", "tier": "verified"})
        b = eng.add_decision({"question": "Which queue strategy?", "choice": "bounded", "tier": "staging"})
    else:
        a = eng.add_playbook({"title": "Configure cache headers", "steps": [{"action": "cache"}]})
        with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
            b = eng.add_playbook({"title": "Drain queued jobs", "steps": [{"action": "drain"}]})
    primary, secondary = (b, a) if pending_side == "primary" else (a, b)
    before = _digest(eng.root)
    if route == "mcp":
        result = json.loads(asyncio.run(mcp_server.merge_knowledge(
            primary["id"], secondary["id"], primary_expected_version=1, secondary_expected_version=1)))
    else:
        with write_provenance.origin_scope(write_provenance.ORIGIN_MCP):
            result = eng.merge_knowledge(primary["id"], secondary["id"],
                                         primary_expected_version=1, secondary_expected_version=1)
    assert result.get("error") == "local_review_only", result
    assert _digest(eng.root) == before


def test_local_pending_merge_remains_available(tmp_path):
    eng = Engram(root=tmp_path / "store")
    a = eng.add_lesson("Keep deployment manifests under version control", tier="staging")
    b = eng.add_lesson("Bound retry budgets for background consumers", tier="staging")
    assert eng.merge_knowledge(a["id"], b["id"])["success"] is True
