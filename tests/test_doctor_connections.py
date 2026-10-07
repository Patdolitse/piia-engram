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
from piia_engram import claude_code_mcp as M
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
    _write(home / ".claude.json", json.dumps({"userID": _SECRET, "projects": {
        str(tmp_path / "proj"): {"mcpServers": {"engram": _entry(store)}, "history": [_SECRET]}}}))
    _checkpoint(store, "claude_code", "auto-2026-10-05T10-00-00-cp1.md", 20)
    _checkpoint(store, "claude_code", "auto-2026-10-05T10-00-00.md", 27)
    _checkpoint(store, "claude_code", "auto-2026-10-06T09-00-00.md", 4)
    _checkpoint(store, "claude_code", "hook-2026-10-06T09-30-00.md", 99)  # a hook, not MCP
    _checkpoint(store, "cursor", "hook-2026-10-06.md", 50)  # Cursor's stop hook: not a call
    _checkpoint(store, "cursor", "auto-2026-09-01T08-00-00.md", 6, age_days=30)
    return {"store": store, "home": home}


def _by_tool(report: dict) -> dict[str, dict]:
    return {row["tool_id"]: row for row in report["clients"]}


def _snapshot(*roots: Path) -> dict[str, tuple]:
    """Every file (bytes + mtime_ns) and every directory (mtime_ns) under ``roots``."""
    shot: dict[str, tuple] = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                shot[str(p)] = ("file", hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            elif p.is_dir():
                shot[str(p)] = ("dir", p.stat().st_mtime_ns)
    return shot


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


def test_mcp_doctor_tool_does_not_carry_the_connection_report(world, monkeypatch):
    import asyncio

    from piia_engram import mcp_server

    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    monkeypatch.setattr(mcp_server, "_engram", Engram(root=world["store"]))
    for fmt in ("markdown", "json"):
        out = asyncio.run(mcp_server.doctor(output_format=fmt))
        for marker in ("Client Connections", "configured_no_calls", "connected", "claude.json",
                       "mcp.json", "verdict", _SECRET):
            assert marker not in out, (fmt, marker)


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


def test_claude_dot_mcp_json_alone_is_an_old_location(home, tmp_path):
    # Claude Code does not read ~/.claude/.mcp.json: an entry there is not "configured".
    _write(home / ".claude" / ".mcp.json", json.dumps({"mcpServers": {"engram": _entry(tmp_path / "store")}}))
    _claude_json(home, {"mcpServers": {}})
    report = C.build_report(tmp_path / "store", days=14)
    row = _by_tool(report)["claude_code"]
    assert row["config_status"] == "legacy_only"
    assert row["verdict"] == "legacy_location"
    assert row["config_path"] == "~/.claude/.mcp.json"
    text = "\n".join(C.render_text(report))
    assert "does not read" in text and "engram setup" in text
    assert _SECRET not in text


def test_setup_never_writes_the_claude_user_config(home, tmp_path):
    claude_json = _claude_json(home, {"mcpServers": {"engram": _entry(tmp_path / "store")}})
    (home / ".claude").mkdir()
    before = claude_json.read_bytes()

    assert W._tool_configs()["claude_code"]["config_paths"] == [home / ".claude.json"]
    tool = next(t for t in W._detect_tools() if t["id"] == "claude_code")
    assert tool["register_via"] == "claude_cli"
    with pytest.raises(ValueError):
        W._write_tool_mcp_config(tool, sys.executable, "piia_engram.mcp_server",
                                 str(tmp_path / "store"), file_safety_root=tmp_path / "store",
                                 authorized_external_write=True)

    assert not (home / ".claude" / ".mcp.json").exists()
    assert claude_json.read_bytes() == before


# ---------------------------------------------------------------------------
# review follow-ups: labels, unreadable dirs, limits, shared labels
# ---------------------------------------------------------------------------


def _rewrite_lessons(store: Path, mutate) -> None:
    path = store / "knowledge" / "lessons.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    mutate(rows)
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_a_stored_client_name_is_relabeled_never_echoed(world):
    def poison(rows):
        rows[0]["provenance"]["client"] = "evil\x1b[2J\nINJECTED line"

    _rewrite_lessons(world["store"], poison)
    report = C.build_report(world["store"], days=14)
    dumped = json.dumps(report, ensure_ascii=False) + "\n".join(C.render_text(report))
    assert "\x1b" not in dumped and "INJECTED" not in dumped and "evil" not in dumped
    assert report["other_activity"]["other"]["writes"] == 1


def test_an_unreadable_directory_is_skipped(world, monkeypatch):
    real_iterdir = Path.iterdir
    blocked = {world["store"] / "contexts" / "claude_code", world["store"] / "playbooks"}

    def iterdir(self):
        if self in blocked:
            raise PermissionError(13, "denied", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    rows = _by_tool(C.build_report(world["store"], days=14))
    assert rows["claude_code"]["verdict"] == "connected"  # the MCP write still counts
    assert rows["claude_code"]["sessions"] == 0


def test_json_reports_an_error_type_only(world, monkeypatch, capsys):
    secret_path = str(world["home"] / "private-dir-name")

    def boom(*args, **kwargs):
        raise PermissionError(13, "denied", secret_path)

    monkeypatch.setattr(C, "build_report", boom)
    code = doctor.run_doctor_json(days=14)
    out = capsys.readouterr().out
    assert code == 1
    assert json.loads(out) == {"error": "PermissionError", "read_only": True}
    assert "private-dir-name" not in out


def test_full_doctor_prints_only_the_error_type(world, monkeypatch):
    secret_path = str(world["home"] / "private-dir-name")

    def boom(*args, **kwargs):
        raise PermissionError(13, "denied", secret_path)

    monkeypatch.setattr(C, "build_report", boom)
    buf = io.StringIO()

    def run():
        with redirect_stdout(buf):
            doctor.run_doctor(fix=False)

    _without_mcp_server_module(run)
    out = buf.getvalue()
    assert "Client connection check skipped (PermissionError)" in out
    assert "private-dir-name" not in out


def test_shared_label_client_without_activity_is_configured_no_calls(home, tmp_path):
    store = tmp_path / "store"
    Engram(root=store)
    _write(home / ".trae" / "mcp.json", json.dumps({"mcpServers": {"engram": _entry(store)}}))
    report = C.build_report(store, days=14)
    assert _by_tool(report)["trae"]["verdict"] == "configured_no_calls"
    assert "Trae (other): configured, no Engram calls" in "\n".join(C.render_text(report))

    _checkpoint(store, "trae", "auto-2026-10-06T08-00-00.md", 2)
    assert _by_tool(C.build_report(store, days=14))["trae"]["verdict"] == "configured_unattributed"


def test_session_counts_are_capped(world):
    _checkpoint(world["store"], "claude_code", "auto-2026-10-06T10-00-00.md", 10**12)
    row = _by_tool(C.build_report(world["store"], days=14))["claude_code"]
    assert row["calls"] == 27 + 4 + C.MAX_SESSION_CALLS
    assert "save_agent_context" in C.SELF_REPORTED_NOTE


def test_huge_days_do_not_overflow(world):
    report = C.build_report(world["store"], days=10**9)
    assert report["days"] == C.MAX_DAYS


def test_an_oversized_claude_user_config_is_undetermined(home, tmp_path, monkeypatch):
    store = tmp_path / "store"
    Engram(root=store)
    _claude_json(home, {"mcpServers": {"engram": _entry(store)}})
    monkeypatch.setattr(M, "MAX_USER_CONFIG_BYTES", 10)
    report = C.build_report(store, days=14)
    row = _by_tool(report)["claude_code"]
    assert row["config_status"] == "undetermined"
    assert "could not be checked" in "\n".join(C.render_text(report))


@pytest.mark.parametrize("argv,days", [(["doctor", "--json", "--days=30"], 30),
                                       (["doctor", "--json", "--days", "3650"], 3650)])
def test_cli_days_forms(world, monkeypatch, capsys, argv, days):
    monkeypatch.setattr(sys, "argv", ["engram", *argv])
    with pytest.raises(SystemExit) as exc:
        W.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["days"] == days


@pytest.mark.parametrize("argv", [["doctor", "--days", "3651"], ["doctor", "--days=99999999999999999999"],
                                  ["doctor", "--days="], ["doctor", "--days"]])
def test_cli_days_out_of_range(world, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["engram", *argv])
    with pytest.raises(SystemExit) as exc:
        W.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", [["doctor", "--days", "\u00b2"], ["doctor", "--days=\u00b2"],
                                  ["doctor", "--json", "--days", "\u0663"]])
def test_cli_rejects_non_ascii_digits(world, monkeypatch, capsys, argv):
    monkeypatch.setattr(sys, "argv", ["engram", *argv])
    with pytest.raises(SystemExit) as exc:
        W.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "--days needs a whole number" in err


# ---------------------------------------------------------------------------
# Claude Code in ~/.claude.json: doctor's tool list agrees with the report
# ---------------------------------------------------------------------------


def _doctor_text(fix: bool = False) -> str:
    buf = io.StringIO()

    def run():
        with redirect_stdout(buf):
            doctor.run_doctor(fix=fix)

    _without_mcp_server_module(run)
    return buf.getvalue()


def _claude_tool() -> dict | None:
    return next((t for t in doctor._detect_installed_tools() if t["tool_id"] == "claude_code"), None)


def _claude_layout(home: Path, store: Path, *, user_config: str | None, dot_mcp: bool) -> None:
    """user_config: None (no ~/.claude.json), "top", "project" or "other"."""
    Engram(root=store)
    if dot_mcp:
        _write(home / ".claude" / ".mcp.json", json.dumps({"mcpServers": {"engram": _entry(store)}}))
    if user_config == "top":
        _claude_json(home, {"mcpServers": {"engram": _entry(store)}, "userID": _SECRET})
    elif user_config == "project":
        _claude_json(home, {"projects": {str(home / "proj"): {
            "mcpServers": {"engram": _entry(store)}, "history": [_SECRET]}}})
    elif user_config == "other":
        _claude_json(home, {"mcpServers": {"other": {"command": _SECRET}}})


@pytest.mark.parametrize("where", ["top", "project"])
def test_doctor_counts_claude_user_config_as_configured(home, tmp_path, where):
    store = tmp_path / "store"
    _claude_layout(home, store, user_config=where, dot_mcp=False)

    tool = _claude_tool()
    assert tool is not None and tool["status"] == "configured"
    assert tool["detect_only"] is True
    # Nothing from the file is kept.
    assert tool["config_path"] == home / ".claude.json"
    assert tool["servers"] == {} and tool["config"] == {}
    assert _by_tool(C.build_report(store, days=14))["claude_code"]["config_status"] == "configured"

    out = _doctor_text()
    assert "[ok] Claude Code" in out and "~/.claude.json" in out
    assert "Claude Code — Engram NOT configured" not in out
    assert "- Claude Code (" not in out  # not listed under "Run 'engram setup'"
    assert "Claude Code (claude_code): configured" in out
    assert _SECRET not in out and str(home / "proj") not in out


def test_doctor_with_only_dot_mcp_json_reports_the_old_location(home, tmp_path):
    store = tmp_path / "store"
    _claude_layout(home, store, user_config=None, dot_mcp=True)

    tool = _claude_tool()
    assert tool["status"] == "legacy" and tool["detect_only"] is True
    assert tool["servers"] == {}
    assert _by_tool(C.build_report(store, days=14))["claude_code"]["config_status"] == "legacy_only"
    out = _doctor_text()
    assert "[ok] Claude Code" not in out
    assert "Claude Code — Engram entry only in ~/.claude/.mcp.json" in out
    assert _SECRET not in out


@pytest.mark.parametrize("user_config", [None, "other"])
def test_doctor_without_any_engram_entry_says_not_configured(home, tmp_path, user_config):
    store = tmp_path / "store"
    (home / ".claude").mkdir()
    _claude_layout(home, store, user_config=user_config, dot_mcp=False)

    tool = _claude_tool()
    assert tool["status"] == "installed"
    assert tool["config_path"] == home / ".claude.json"
    assert _by_tool(C.build_report(store, days=14))["claude_code"]["config_status"] == "not_configured"
    out = _doctor_text()
    assert "Claude Code — Engram NOT configured" in out
    assert "Run 'engram setup' to configure them." in out
    assert _SECRET not in out


def test_doctor_user_config_alone_without_claude_dir_is_detected(home, tmp_path):
    store = tmp_path / "store"
    _claude_layout(home, store, user_config="other", dot_mcp=False)
    tool = _claude_tool()
    assert tool is not None and tool["status"] == "installed"
    assert _by_tool(C.build_report(store, days=14))["claude_code"]["config_status"] == "not_configured"


def test_doctor_with_both_configs_counts_the_user_config_and_notes_the_old_file(home, tmp_path):
    store = tmp_path / "store"
    _claude_layout(home, store, user_config="top", dot_mcp=True)

    tool = _claude_tool()
    assert tool["status"] == "configured" and tool["detect_only"] is True
    assert tool["config_path"] == home / ".claude.json"
    assert tool["servers"] == {}
    row = _by_tool(C.build_report(store, days=14))["claude_code"]
    assert row["config_status"] == "configured" and row["config_path"] == "~/.claude.json"
    out = _doctor_text()
    assert "[ok] Claude Code — Engram configured (in ~/.claude.json)" in out
    assert "~/.claude/.mcp.json still holds an Engram entry" in out
    assert _SECRET not in out


def test_doctor_too_large_user_config_is_undetermined_not_unconfigured(home, tmp_path, monkeypatch):
    store = tmp_path / "store"
    (home / ".claude").mkdir()
    _claude_layout(home, store, user_config="top", dot_mcp=False)
    monkeypatch.setattr(M, "MAX_USER_CONFIG_BYTES", 8)

    tool = _claude_tool()
    assert tool["status"] == "undetermined" and tool["detect_only"] is True
    assert _by_tool(C.build_report(store, days=14))["claude_code"]["config_status"] == "undetermined"
    out = _doctor_text()
    assert "Claude Code — Engram NOT configured" not in out
    assert "- Claude Code (" not in out


def test_doctor_env_check_reads_only_key_names_of_a_user_config_entry(home, tmp_path):
    store = tmp_path / "store"
    _claude_layout(home, store, user_config="top", dot_mcp=False)
    # Only the key names of the entry are looked at: ENGRAM_APPROVAL is missing there.
    findings = doctor._client_env_findings([_claude_tool()], strict=True, user_env={})
    assert [m for _, m in findings] == [{"ENGRAM_APPROVAL": "strict"}]


def test_doctor_fix_never_writes_claude_user_config(home, tmp_path):
    store = tmp_path / "store"
    _claude_layout(home, store, user_config="top", dot_mcp=False)
    claude_json = home / ".claude.json"
    before = claude_json.read_bytes()

    out = _doctor_text(fix=True)

    assert claude_json.read_bytes() == before
    assert not (home / ".claude" / ".mcp.json").exists()
    assert _SECRET not in out

