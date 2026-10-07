"""MCP import_engram only previews; applying an import is a local command.

* native and OpenClaw formats: a request that would write answers
  ``local_only`` with the local command, and nothing is written;
* a preview (``dry_run=true``) returns the plan and writes nothing;
* the local ``engram import`` is unchanged;
* an import that would approve a revision of a pinned entry is refused locally
  too, and a race inside the import returns ``pinned_target`` instead of raising.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.cli_commands import _render_import_result_text, _run_import_backup, run_pin
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    monkeypatch.setattr(mcp_server, "_session", mcp_server._SessionTracker())
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


def _snapshot(root: Path) -> dict[str, str]:
    """Every file of the store except logs."""
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix != ".log" and "sessions" not in p.parts
    }


def _backup(tmp_path: Path, lessons: list[dict], *, name: str = "backup.json", relations=None) -> Path:
    path = tmp_path / name
    knowledge = {"lessons": lessons, "decisions": [], "playbooks": []}
    if relations is not None:
        knowledge["relations"] = relations
    path.write_text(json.dumps({"schema_version": "1.0", "identity": {}, "knowledge": knowledge}),
                    encoding="utf-8")
    return path


def _incoming() -> list[dict]:
    return [{"id": "imp-1", "summary": "A lesson that only exists in the backup", "tier": "verified",
             "status": "active"}]


def test_mcp_preview_returns_the_plan_and_writes_nothing(eng, tmp_path):
    path = _backup(tmp_path, _incoming())
    before = _snapshot(eng.root)
    for merge in (True, False):
        plan = json.loads(_run(mcp_server.import_engram(input_path=str(path), merge=merge, dry_run=True)))
        assert plan["status"] == "preview" and plan["dry_run"] is True
        assert plan["summary"]["lessons"]["incoming"] == 1
    assert _snapshot(eng.root) == before


@pytest.mark.parametrize("merge", [True, False])
def test_mcp_native_import_is_local_only(eng, tmp_path, merge):
    path = _backup(tmp_path, _incoming())
    before = _snapshot(eng.root)
    result = json.loads(_run(mcp_server.import_engram(input_path=str(path), merge=merge)))
    assert result["error"] == "local_only"
    assert "engram import" in result["hint"] and "--apply" in result["hint"]
    assert ("--overwrite" in result["hint"]) is (not merge)
    assert _snapshot(eng.root) == before
    assert eng._find_item_by_id("imp-1") == (None, None)


def test_mcp_openclaw_import_is_local_only(eng, tmp_path, monkeypatch):
    memory = tmp_path / "MEMORY.md"
    memory.write_text("- an OpenClaw memory line that must not be imported over MCP\n", encoding="utf-8")
    called = []
    monkeypatch.setattr(mcp_server, "import_from_openclaw", lambda *a, **k: called.append(1) or {})
    before = _snapshot(eng.root)
    result = json.loads(_run(mcp_server.import_engram(format="openclaw", memory_path=str(memory))))
    assert result["error"] == "local_only" and called == []
    preview = json.loads(_run(mcp_server.import_engram(format="openclaw", memory_path=str(memory),
                                                       dry_run=True)))
    assert preview["status"] == "preview" and preview["files"]["memory"]["bullets"] == 1
    assert "OpenClaw memory line" not in json.dumps(preview)
    assert called == [] and _snapshot(eng.root) == before


def test_local_import_still_applies(eng, tmp_path, capsys):
    path = _backup(tmp_path, _incoming())
    assert _run_import_backup([str(path), "--apply", "--yes"]) == 0
    assert eng._find_item_by_id("imp-1")[1] is not None
    capsys.readouterr()


def test_local_import_refuses_to_promote_a_revision_of_a_pinned_entry(eng, tmp_path):
    pinned = eng.add_lesson({"summary": "Pinned lesson a local replace import would supersede",
                             "domain": "workflow", "tier": "verified"})
    assert run_pin([pinned["id"]]) == 0
    proposal = eng.add_lesson({"summary": "Pending revision of the pinned lesson", "domain": "workflow",
                               "supersedes": pinned["id"]})
    stored = eng._find_item_by_id(proposal["id"])[1]
    promoted = {**stored, "tier": "verified", "memory_state": "verified", "approval_status": "approved"}
    path = _backup(tmp_path, [eng._find_item_by_id(pinned["id"])[1], promoted])
    before = _snapshot(eng.root)
    result = eng.import_all(str(path), merge=False)
    assert result["error"] == "pinned_target" and result["targets"] == [pinned["id"]]
    assert _snapshot(eng.root) == before
    assert eng._find_item_by_id(pinned["id"])[1]["pinned"] is True


def test_an_import_race_returns_pinned_target_instead_of_raising(eng, tmp_path, monkeypatch):
    from piia_engram import pinning

    path = _backup(tmp_path, _incoming())

    def _boom(*args, **kwargs):
        raise pinning.PinnedTargetRefused("lesson", ["pinned-x"])

    monkeypatch.setattr(eng, "_update_entries", _boom)
    result = eng.import_all(str(path), merge=True)
    assert result["status"] == "refused" and result["error"] == "pinned_target"
    assert result["targets"] == ["pinned-x"]
    assert "resume" in result["message"].lower() or "again" in result["message"].lower()


def test_import_text_counts_skipped_and_link_protected_entries_apart(eng, tmp_path):
    skipped = eng.add_lesson({"summary": "Pinned lesson the backup also carries", "domain": "workflow",
                              "tier": "verified"})
    linked = eng.add_lesson({"summary": "Pinned lesson a backup link points at", "domain": "workflow",
                             "tier": "verified"})
    for item in (skipped, linked):
        assert run_pin([item["id"]]) == 0
    incoming = [{"id": skipped["id"], "summary": "Other text, same id", "tier": "verified", "status": "active"},
                {"id": "imp-2", "summary": "A backup lesson linking to a pin", "tier": "verified",
                 "status": "active"}]
    path = _backup(tmp_path, incoming, relations=[{"src": "imp-2", "rel": "supersedes", "dst": linked["id"]}])
    text = _render_import_result_text(eng.import_all(str(path), merge=True, dry_run=True))
    assert "pinned: 1 entry skipped" in text
    assert "1 entry protected from a supersedes link" in text
    assert "imp-2 -> " + linked["id"] in text
