"""Reads never rewrite identity files; start-up keeps knowledge and identity as they are.

* get_trust_boundaries (behind get_user_context, the trust_boundaries
  resource, get_profile(safe=True)) fills missing defaults in memory only;
  identity/trust_boundaries.json is written by the store's own initialisation
  and by update_trust_boundaries, never by a read;
* a server start plus get_user_context / get_resume_brief / search_knowledge on
  an already-initialised store leaves knowledge, identity and playbooks
  byte-identical;
* get_lessons over MCP may update read bookkeeping (access_count,
  last_reviewed) but never knowledge content; the connection report does not
  call the start "zero write".
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import connection_report, mcp_server
from piia_engram.core import Engram

_BOOKKEEPING = {"access_count", "last_reviewed", "last_accessed", "last_accessed_at"}


@pytest.fixture()
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    seed = Engram(root=root)
    seed.update_profile({"role": "developer"})
    seed.add_lesson("Keep fixtures in a temporary store so suites never share state", domain="testing")
    seed.add_decision("Which test runner do we use?", choice="pytest", reasoning="fixtures")
    seed.add_playbook({"title": "Run the release checklist", "steps": [{"action": "check"}]})
    return root


def _run(coro):
    return asyncio.run(coro)


def _digest(root: Path, subdirs=("knowledge", "identity", "playbooks")) -> dict[str, str]:
    out = {}
    for sub in subdirs:
        base = root / sub
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file() and path.name != ".engram-write.lock":
                    out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _drop_one_default(root: Path) -> None:
    """An older store: one trust-boundary default is missing on disk."""
    path = root / "identity" / "trust_boundaries.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    key = sorted(k for k in data if k != "updated_at")[0]
    data.pop(key)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_reads_fill_trust_boundary_defaults_in_memory_only(root, monkeypatch):
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    _drop_one_default(root)
    before = _digest(root, ("identity",))
    full = engram.get_trust_boundaries()
    from piia_engram.storage import DEFAULT_TRUST_BOUNDARIES

    assert set(DEFAULT_TRUST_BOUNDARIES) <= set(full)
    engram.get_profile(safe=True)
    _run(mcp_server.get_user_context(level="full"))
    _run(mcp_server.get_resume_brief())
    assert _digest(root, ("identity",)) == before


def test_read_only_handle_reads_trust_boundaries(root):
    _drop_one_default(root)
    before = _digest(root, ("identity",))
    reader = Engram(root=root, read_only=True)
    assert reader.get_trust_boundaries()
    assert _digest(root, ("identity",)) == before


def test_server_start_and_reads_keep_an_initialised_store(root, monkeypatch):
    before = _digest(root)
    engram, error = mcp_server._init_engram(root)
    assert error is None
    monkeypatch.setattr(mcp_server, "_engram", engram)
    _run(mcp_server.get_user_context())
    _run(mcp_server.get_user_context(level="full"))
    _run(mcp_server.get_resume_brief())
    _run(mcp_server.search_knowledge(query="fixtures"))
    assert _digest(root) == before


def test_get_lessons_changes_only_access_bookkeeping(root, monkeypatch):
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    path = root / "knowledge" / "lessons.json"
    strip = lambda rows: [{k: v for k, v in r.items() if k not in _BOOKKEEPING} for r in rows]  # noqa: E731
    before = strip(json.loads(path.read_text(encoding="utf-8")))
    _run(mcp_server.get_lessons())
    assert strip(json.loads(path.read_text(encoding="utf-8"))) == before


def test_connection_report_does_not_claim_a_zero_write_start(root):
    line = connection_report.startup_line(root)
    assert line["state"] != "zero_write"
    assert "imports nothing" in line["detail"]
    text = "\n".join(connection_report.render_text(connection_report.build_report(root, days=14)))
    assert "Startup writes: none" not in text
