"""engram doctor: are the AI clients connected, and do they actually call Engram?

Built on a test store and a temporary home: client configs, session
checkpoints the MCP server writes, and rows written over MCP. Doctor must
report "connected with calls / configured but never called / not configured",
print nothing from a config entry, and leave the store and the home directory
byte-for-byte unchanged.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import connection_report as C
from piia_engram import doctor
from piia_engram.core import Engram
from piia_engram.write_provenance import origin_scope

_SECRET = "placeholder-value-doctor-must-not-print"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(h / "AppData" / "Local"))
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("ENGRAM_NO_AUTO_BACKUP", "1")
    for var in ("ENGRAM_APPROVAL", "ENGRAM_RECONCILE", "ENGRAM_MCP_STARTUP_SYNC",
                "ENGRAM_GOVERNANCE", "ENGRAM_CLIENT_TYPE"):
        monkeypatch.delenv(var, raising=False)
    return h


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _entry(store: Path) -> dict:
    return {"command": sys.executable, "args": ["-m", "piia_engram.mcp_server"],
            "env": {"ENGRAM_DIR": str(store), "API_TOKEN": _SECRET}}


def _checkpoint(store: Path, tool: str, name: str, calls: int, *, age_days: float = 0.0) -> Path:
    path = _write(store / "contexts" / tool / name,
                  f"# Session: {tool} @ 2026-10-05 10:00\n\n### 10:00\n"
                  f"[MCP 自动记录] 会话时长: 3 分钟\n工具调用次数: {calls}\n"
                  f"使用的工具: get_user_context, search_knowledge\n\n#### Actions\n"
                  f"1. `search_knowledge` — private query text {_SECRET}\n")
    if age_days:
        moment = time.time() - age_days * 86400
        os.utime(path, (moment, moment))
    return path


@pytest.fixture
def world(home: Path, tmp_path: Path) -> dict:
    """Claude Code: configured and used; Cursor: configured, only old or hook traces;
    Codex: installed, not configured; Windsurf and the rest: not installed."""
    store = tmp_path / "store"
    eng = Engram(root=store)
    with origin_scope("mcp", client_name="claude-code", client_version="2.1.0"):
        eng.add_lesson({"summary": "Rotate the build cache weekly to keep disks free"})
    del eng
    _write(home / ".claude" / ".mcp.json", json.dumps({"mcpServers": {"engram": _entry(store)}}))
    _write(home / ".cursor" / "mcp.json", json.dumps({"mcpServers": {"engram": _entry(store)}}))
    _write(home / ".codex" / "config.toml", '[mcp_servers.other]\ncommand = "other"\n')
    _checkpoint(store, "claude_code", "auto-2026-10-05T10-00-00-cp1.md", 20)
    _checkpoint(store, "claude_code", "auto-2026-10-05T10-00-00.md", 27)
    _checkpoint(store, "claude_code", "auto-2026-10-06T09-00-00.md", 4)
    _checkpoint(store, "claude_code", "hook-2026-10-06T09-30-00.md", 99)  # a hook, not MCP
    _checkpoint(store, "cursor", "hook-2026-10-06.md", 50)  # Cursor's stop hook: not a call
    _checkpoint(store, "cursor", "auto-2026-09-01T08-00-00.md", 6, age_days=30)
    return {"store": store, "home": home}


def _by_tool(report: dict) -> dict[str, dict]:
    return {row["tool_id"]: row for row in report["clients"]}


def _snapshot(*roots: Path) -> dict[str, str]:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for root in roots for p in sorted(root.rglob("*")) if p.is_file()
    }


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------


def test_report_tells_connected_configured_and_unconfigured_apart(world):
    report = C.build_report(world["store"], days=14)
    rows = _by_tool(report)

    claude = rows["claude_code"]
    assert claude["verdict"] == "connected"
    assert claude["client"] == "claude_code"
    assert claude["sessions"] == 2  # the -cp1 file belongs to the first session
    assert claude["calls"] == 27 + 4  # the largest counter per session; hooks do not count
    assert claude["writes"] == 1
    assert claude["last_seen"]

    assert rows["cursor"]["verdict"] == "configured_no_calls"
    assert rows["codex"]["verdict"] == "not_configured"
    assert rows["windsurf"]["verdict"] == "not_installed"
    assert report["read_only"] is True


def test_days_widens_the_window(world):
    rows = _by_tool(C.build_report(world["store"], days=45))
    assert rows["cursor"]["verdict"] == "connected"
    assert rows["cursor"]["calls"] == 6


def test_calls_from_a_client_without_a_known_config_are_still_shown(world):
    _checkpoint(world["store"], "codex", "auto-2026-10-06T11-00-00.md", 3)
    rows = _by_tool(C.build_report(world["store"], days=14))
    assert rows["codex"]["verdict"] == "calls_without_config"


def test_unlabeled_clients_are_listed_separately(world):
    _checkpoint(world["store"], "mcp_auto", "auto-2026-10-06T12-00-00.md", 2)
    report = C.build_report(world["store"], days=14)
    assert report["other_activity"]["unknown"]["sessions"] == 1


def test_text_names_each_verdict_and_the_next_step(world):
    lines = C.render_text(C.build_report(world["store"], days=14))
    text = "\n".join(lines)
    assert "[ok] Claude Code (claude_code): connected" in text
    assert "Cursor (cursor): configured, no Engram calls in the last 14 days -- restart Cursor" in text
    assert "~/.cursor/mcp.json" in text
    assert "Codex (codex): not configured -- run 'engram setup'" in text
    assert "Not installed:" in text and "Windsurf" in text
    assert "self-reported" in text


# ---------------------------------------------------------------------------
# strict / startup lines
# ---------------------------------------------------------------------------


def test_strict_and_startup_lines(world, monkeypatch):
    store = world["store"]
    report = C.build_report(store, days=14)
    assert report["strict_approval"]["state"] == "off"
    assert report["startup_writes"]["variables"]["ENGRAM_RECONCILE"]["meaning"] == "unset"

    (store / "approval_mode.json").write_text('{"strict_first_seen_at": "2026-09-26T00:00:00Z"}',
                                              encoding="utf-8")
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    monkeypatch.setenv("ENGRAM_MCP_STARTUP_SYNC", "1")
    (store / "telemetry_config.json").write_text('{"reconcile_authorized": false}', encoding="utf-8")
    report = C.build_report(store, days=14)
    assert report["strict_approval"]["state"] == "on"
    variables = report["startup_writes"]["variables"]
    assert variables["ENGRAM_RECONCILE"]["value"] == "0"
    assert variables["ENGRAM_MCP_STARTUP_SYNC"]["meaning"] == "no effect"
    assert variables["reconcile_authorized"]["value"] == "false"
    text = "\n".join(C.render_text(report))
    assert "Strict approval: on for every client" in text
    assert "ENGRAM_RECONCILE=0" in text and "imports nothing" in text


def test_strict_from_this_shell_only(world, monkeypatch):
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    assert C.build_report(world["store"], days=14)["strict_approval"]["state"] == "on_here"


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------


def test_report_shows_nothing_from_config_entries_or_checkpoints(world):
    report = C.build_report(world["store"], days=14)
    dumped = json.dumps(report, ensure_ascii=False) + "\n".join(C.render_text(report))
    assert _SECRET not in dumped
    assert "private query text" not in dumped
    assert str(world["home"]) not in dumped
    assert str(world["store"]) not in dumped
    assert sys.executable not in dumped


# ---------------------------------------------------------------------------
# doctor wiring, --json and zero writes
# ---------------------------------------------------------------------------


def _without_mcp_server_module(fn):
    """Doctor imports the MCP server read-only; put the suite's module back afterwards."""
    import piia_engram

    saved = sys.modules.pop("piia_engram.mcp_server", None)
    try:
        return fn()
    finally:
        sys.modules.pop("piia_engram.mcp_server", None)
        if saved is not None:
            sys.modules["piia_engram.mcp_server"] = saved
            piia_engram.mcp_server = saved
        elif hasattr(piia_engram, "mcp_server"):
            delattr(piia_engram, "mcp_server")


def test_doctor_prints_the_section_and_writes_nothing(world, monkeypatch):
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.delenv("ENGRAM_TEST", raising=False)
    before = _snapshot(world["store"], world["home"])

    buf = io.StringIO()

    def run():
        with redirect_stdout(buf):
            doctor.run_doctor(fix=False)

    _without_mcp_server_module(run)

    out = buf.getvalue()
    assert _snapshot(world["store"], world["home"]) == before
    assert "Client Connections (last 14 days)" in out
    assert "Claude Code (claude_code): connected" in out
    assert "Cursor (cursor): configured, no Engram calls" in out
    assert _SECRET not in out


def test_doctor_json_prints_only_the_report_and_writes_nothing(world, monkeypatch):
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    monkeypatch.delenv("ENGRAM_TEST", raising=False)
    before = _snapshot(world["store"], world["home"])

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = doctor.run_doctor_json(days=45)

    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["days"] == 45
    assert _by_tool(payload)["cursor"]["verdict"] == "connected"
    assert _snapshot(world["store"], world["home"]) == before
    assert _SECRET not in buf.getvalue()


def test_cli_parses_days_and_json(world, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["engram", "doctor", "--json", "--days", "45"])
    with pytest.raises(SystemExit) as exc:
        W.main()
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["days"] == 45


@pytest.mark.parametrize("argv", [["doctor", "--days", "x"], ["doctor", "--days", "0"],
                                  ["doctor", "--json", "--fix"]])
def test_cli_refuses_bad_arguments(world, monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["engram", *argv])
    with pytest.raises(SystemExit) as exc:
        W.main()
    assert exc.value.code == 2


def test_mcp_doctor_tool_does_not_carry_the_connection_report(world):
    import inspect

    from piia_engram import mcp_tools_admin

    assert "connection_report" not in inspect.getsource(mcp_tools_admin.doctor)


# ---------------------------------------------------------------------------
# Claude Code's user config (~/.claude.json): detected for the report only
# ---------------------------------------------------------------------------


def _claude_json(home: Path, payload: dict) -> Path:
    return _write(home / ".claude.json", json.dumps(payload))


def test_claude_user_config_top_level_entry_counts_as_configured(home, tmp_path):
    _claude_json(home, {"mcpServers": {"engram": _entry(tmp_path / "store")}, "userID": _SECRET})
    rows = _by_tool(C.build_report(tmp_path / "store", days=14))
    assert rows["claude_code"]["config_status"] == "configured"
    assert rows["claude_code"]["config_path"] == "~/.claude.json"


def test_claude_user_config_project_entry_counts_as_configured(home, tmp_path):
    _claude_json(home, {"projects": {str(tmp_path / "proj"): {
        "mcpServers": {"engram": _entry(tmp_path / "store")}, "history": [_SECRET]}}})
    report = C.build_report(tmp_path / "store", days=14)
    assert _by_tool(report)["claude_code"]["config_status"] == "configured"
    dumped = json.dumps(report) + "\n".join(C.render_text(report))
    assert _SECRET not in dumped and "proj" not in dumped


def test_claude_user_config_without_engram_is_not_configured(home, tmp_path):
    _claude_json(home, {"mcpServers": {"other": {"command": _SECRET}}, "projects": {"x": {}}})
    report = C.build_report(tmp_path / "store", days=14)
    assert _by_tool(report)["claude_code"]["config_status"] == "not_configured"
    assert _SECRET not in json.dumps(report)


def test_claude_dot_mcp_json_is_still_detected(home, tmp_path):
    _write(home / ".claude" / ".mcp.json", json.dumps({"mcpServers": {"engram": _entry(tmp_path / "store")}}))
    _claude_json(home, {"mcpServers": {}})
    row = _by_tool(C.build_report(tmp_path / "store", days=14))["claude_code"]
    assert row["config_status"] == "configured"
    assert row["config_path"] == "~/.claude/.mcp.json"


def test_user_config_detection_does_not_change_where_setup_writes(home, tmp_path):
    claude_json = _claude_json(home, {"mcpServers": {"engram": _entry(tmp_path / "store")}})
    (home / ".claude").mkdir()
    before = claude_json.read_bytes()

    assert W._tool_configs()["claude_code"]["config_paths"] == [home / ".claude" / ".mcp.json"]
    assert all(Path(t["config_path"]).name != ".claude.json" for t in doctor._detect_installed_tools())
    tool = next(t for t in W._detect_tools() if t["id"] == "claude_code")
    assert tool["config_path"] == home / ".claude" / ".mcp.json"
    W._write_tool_mcp_config(tool, sys.executable, "piia_engram.mcp_server",
                             str(tmp_path / "store"), file_safety_root=tmp_path / "store",
                             authorized_external_write=True)

    assert (home / ".claude" / ".mcp.json").is_file()
    assert claude_json.read_bytes() == before
