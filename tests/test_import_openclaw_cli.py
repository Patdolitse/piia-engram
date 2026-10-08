"""engram import --format openclaw: the local way to import OpenClaw files.

Preview by default (metadata only); --apply --yes writes: MEMORY.md lessons go
to the review queue with a receipt and an audit line, USER.md / SOUL.md create
pending profile, preference and quality-standard proposals. The
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
    assert eng.get_profile().get("role") != "release engineer"
    assert eng.get_identity_proposals()[0]["updates"]["role"] == "release engineer"
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
    assert str(f["memory"]) not in result["hint"] and "--memory <MEMORY.md>" in result["hint"]


# ---------------------------------------------------------------------------
# the refusal never echoes a caller's path; odd paths are refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["C:/my files/MEMORY.md", "a;rm -rf x.md", "$(whoami).md"])
def test_local_only_hints_use_placeholders(eng, raw):
    for call in (mcp_server.import_engram(format="openclaw", memory_path=raw),
                 mcp_server.import_engram(input_path=raw)):
        result = json.loads(asyncio.run(call))
        assert result["error"] == "local_only"
        assert raw not in result["hint"] and "<" in result["hint"]


@pytest.mark.parametrize("raw", ["MEMORY.md\nrm -rf /", "MEMORY\x00.md", "MEMORY\r.md"])
def test_paths_with_control_characters_are_refused(eng, raw):
    for call in (mcp_server.import_engram(format="openclaw", memory_path=raw),
                 mcp_server.import_engram(format="openclaw", memory_path=raw, dry_run=True),
                 mcp_server.import_engram(input_path=raw)):
        result = json.loads(asyncio.run(call))
        assert "error" in result and result["error"] != "local_only", result


# ---------------------------------------------------------------------------
# one reader for preview and import; everything is read before anything is written
# ---------------------------------------------------------------------------


def test_home_relative_paths_preview_and_import_alike(eng, tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "MEMORY.md").write_text("## Lessons learned\n- keep the release notes short\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    code, preview = _cli(capsys, "--memory", "~/MEMORY.md")
    assert code == 0 and preview["files"]["memory"] == {"exists": True, "bullets": 1}
    code, out = _cli(capsys, "--memory", "~/MEMORY.md", "--apply", "--yes")
    assert code == 0 and out["status"] == "success"
    assert [l["summary"] for l in eng.get_lessons(limit=None, _update_access=False)] == [
        "keep the release notes short"]


def test_an_unreadable_file_stops_the_import_before_any_write(eng, tmp_path, capsys):
    memory = tmp_path / "MEMORY.md"
    memory.write_text("## Lessons learned\n- a lesson that must not be half-imported\n", encoding="utf-8")
    soul = tmp_path / "SOUL.md"
    soul.write_text("## Work preferences\n- editor: vim\n", encoding="utf-8")
    user = tmp_path / "USER.md"
    user.write_bytes("- Role: caf\xe9 owner\n".encode("latin-1"))
    profile, prefs = eng.get_profile(), eng.get_preferences()
    code, out = _cli(capsys, "--memory", str(memory), "--soul", str(soul), "--user", str(user),
                     "--apply", "--yes")
    assert code == 1 and "error" in out
    assert eng.get_lessons(limit=None, _update_access=False) == []
    assert eng.get_profile() == profile and eng.get_preferences() == prefs


def test_preview_reports_a_directory_and_needs_a_file(eng, tmp_path, capsys):
    code, out = _cli(capsys, "--memory", str(tmp_path))
    assert out["files"]["memory"] == {"exists": True, "error": "not a file"}
    from piia_engram.compat import preview_openclaw

    assert "error" in preview_openclaw(eng)
