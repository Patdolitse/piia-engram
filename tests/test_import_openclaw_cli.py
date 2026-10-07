"""engram import --format openclaw: the local way to import OpenClaw files.

Preview by default (metadata only); --apply --yes writes: MEMORY.md lessons go
to the review queue with a receipt and an audit line, USER.md / SOUL.md merge
into the profile, preferences and quality standards as before. The
ENGRAM_RECONCILE=0 switch stops it before any file is read. The MCP refusal
names this command.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.cli_commands import _run_import_backup
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.delenv("ENGRAM_RECONCILE", raising=False)
    engram = Engram(root=root)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _files(tmp_path: Path) -> dict[str, Path]:
    memory = tmp_path / "MEMORY.md"
    memory.write_text("## Lessons learned\n- [python] pin the lockfile before a release build\n",
                      encoding="utf-8")
    user = tmp_path / "USER.md"
    user.write_text("- Role: release engineer\n", encoding="utf-8")
    return {"memory": memory, "user": user}


def _cli(capsys, *args) -> tuple[int, dict]:
    capsys.readouterr()
    code = _run_import_backup(["--format", "openclaw", *args, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_openclaw_preview_writes_nothing(eng, tmp_path, capsys):
    f = _files(tmp_path)
    code, out = _cli(capsys, "--memory", str(f["memory"]), "--user", str(f["user"]))
    assert code == 0 and out["status"] == "preview" and out["files"]["memory"]["bullets"] == 1
    assert eng.get_lessons(limit=None, _update_access=False) == []
    assert eng.get_profile().get("role") != "release engineer"


def test_openclaw_apply_queues_lessons_with_a_receipt(eng, tmp_path, capsys):
    f = _files(tmp_path)
    code, out = _cli(capsys, "--memory", str(f["memory"]), "--user", str(f["user"]), "--apply", "--yes")
    assert code == 0 and out["status"] == "success" and out["receipt"]
    lessons = eng.get_lessons(limit=None, _update_access=False)
    assert [(l["summary"], l["tier"]) for l in lessons] == [("pin the lockfile before a release build", "staging")]
    assert eng.get_profile().get("role") == "release engineer"  # identity: merged as before
    audit = [json.loads(line) for line in (eng.root / "audit.log").read_text(encoding="utf-8").splitlines()]
    assert any(e.get("resource") == "knowledge/import_openclaw" for e in audit)


def test_openclaw_apply_needs_yes(eng, tmp_path, capsys):
    f = _files(tmp_path)
    code, out = _cli(capsys, "--memory", str(f["memory"]), "--apply")
    assert code == 1 and out["requires_confirmation"] is True
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_openclaw_respects_the_reconcile_switch(eng, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    f = _files(tmp_path)
    for extra in ((), ("--apply", "--yes")):
        code, out = _cli(capsys, "--memory", str(f["memory"]), *extra)
        assert out.get("disabled_by") or out.get("status") in ("disabled", "refused"), out
    assert eng.get_lessons(limit=None, _update_access=False) == []


def test_openclaw_needs_a_file(eng, capsys):
    code, out = _cli(capsys)
    assert code == 2 and "error" in out


def test_mcp_refusal_names_the_local_command(eng, tmp_path):
    f = _files(tmp_path)
    result = json.loads(asyncio.run(mcp_server.import_engram(format="openclaw", memory_path=str(f["memory"]))))
    assert result["error"] == "local_only"
    assert "engram import --format openclaw" in result["hint"] and "--apply --yes" in result["hint"]
    assert str(f["memory"]) in result["hint"]
