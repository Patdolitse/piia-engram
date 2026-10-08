"""Claude Code: Engram is registered through the claude command, in the user config.

Claude Code reads user-scope MCP servers from ~/.claude.json (or
$CLAUDE_CONFIG_DIR/.claude.json); it does not read ~/.claude/.mcp.json, where
older setup versions wrote. setup and doctor --fix register through
``claude mcp add --scope user engram ...`` and never write the user config
themselves; without the command they print it.

No test here runs the real ``claude``: the module's ``cli_path`` / ``run_cli``
seams are replaced by a recorder, or by a fake executable on a temporary PATH.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import claude_code_mcp as M
from piia_engram import connection_report as C
from piia_engram import doctor

_SECRET = "placeholder-secret-not-to-print"
PY = "/opt/py/bin/python3"


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_NO_AUTO_BACKUP", "1")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


@pytest.fixture
def config_dir(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """CLAUDE_CONFIG_DIR set to a temporary directory."""
    d = tmp_path / "claude-config"
    d.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    return d


class Recorder:
    """Stands in for the claude command: records argv, answers with a return code."""

    def __init__(self, codes: dict[str, int] | None = None):
        self.calls: list[list[str]] = []
        self.codes = codes or {}

    def __call__(self, argv, timeout=None):
        self.calls.append(list(argv))
        code = self.codes.get(argv[2] if len(argv) > 2 else "", 0)
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="boom\n" if code else "")


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(M, "cli_path", lambda: "/fake/bin/claude")
    monkeypatch.setattr(M, "run_cli", rec)
    return rec


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def _entry(env: dict | None = None) -> dict:
    return {"command": PY, "args": ["-m", "piia_engram.mcp_server"],
            "env": {"PYTHONIOENCODING": "utf-8", "ENGRAM_TOOLS": "all", **(env or {})}}


def _build(existing_env: dict) -> dict:
    return _entry({"ENGRAM_DIR": "/data/engram"})


def _snapshot(*roots: Path) -> dict[str, tuple]:
    shot: dict[str, tuple] = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                shot[str(p)] = ("file", hashlib.sha256(p.read_bytes()).hexdigest())
            elif p.is_dir():
                shot[str(p)] = ("dir",)
    return shot


EXPECTED_ADD = [
    "/fake/bin/claude", "mcp", "add", "--scope", "user", "engram",
    "-e", "PYTHONIOENCODING=utf-8", "-e", "ENGRAM_TOOLS=all", "-e", "ENGRAM_DIR=/data/engram",
    "--", PY, "-m", "piia_engram.mcp_server",
]


# ---------------------------------------------------------------------------
# locations
# ---------------------------------------------------------------------------


def test_user_config_is_home_dot_claude_json(home):
    assert M.user_config_path() == home / ".claude.json"
    assert M.legacy_path() == home / ".claude" / ".mcp.json"
    assert M.user_config_label() == "~/.claude.json"


def test_claude_config_dir_moves_the_user_config(home, config_dir):
    assert M.user_config_path() == config_dir / ".claude.json"
    assert M.config_dir() == config_dir
    assert M.user_config_label() == "$CLAUDE_CONFIG_DIR/.claude.json"
    # The old setup location is under the home directory either way.
    assert M.legacy_path() == home / ".claude" / ".mcp.json"


def test_tool_config_points_at_the_user_config(home, config_dir):
    cfg = W._tool_configs()["claude_code"]
    assert cfg["config_paths"] == [config_dir / ".claude.json"]
    assert cfg["register_via"] == "claude_cli"


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------


def test_cli_available_and_not_registered_runs_add(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"other": {"command": "x"}}})
    before = (home / ".claude.json").read_bytes()

    result = M.register(_build)

    assert result.status == "added"
    assert cli.calls == [EXPECTED_ADD]
    assert (home / ".claude.json").read_bytes() == before


def test_add_runs_when_only_a_project_entry_exists(home, cli):
    _write(home / ".claude.json", {"projects": {"/p": {"mcpServers": {"engram": _entry()}}}})
    assert M.register(_build).status == "added"
    assert cli.calls == [EXPECTED_ADD]


def test_same_entry_already_registered_does_not_add(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"type": "stdio", **_build({})}}})
    result = M.register(_build)
    assert result.status == "unchanged"
    assert cli.calls == []


def test_registered_as_piia_engram_does_not_add_a_second_entry(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"piia-engram": {"command": "piia-engram-mcp"}}})
    result = M.register(_build)
    assert result.status == "present" and result.name == "piia-engram"
    assert cli.calls == []


def test_no_cli_writes_nothing_and_returns_the_manual_command(home, tmp_path):
    before = _snapshot(home)
    result = M.register(_build)
    assert result.status == "manual" and result.detail == "no_cli"
    assert result.command.startswith("claude mcp add --scope user engram -e PYTHONIOENCODING=utf-8")
    assert result.command.endswith(f" {PY} -m piia_engram.mcp_server")
    assert _snapshot(home) == before


def test_manual_command_names_owner_env_keys_without_their_values(home):
    _write(home / ".claude" / ".mcp.json", {"mcpServers": {"engram": _entry({"API_TOKEN": _SECRET})}})
    result = M.register(lambda env: _entry(env))
    assert result.status == "manual"
    assert _SECRET not in result.command
    assert result.hidden_env == ["API_TOKEN"]


def test_owner_env_from_the_old_entry_is_passed_to_add(home, cli):
    _write(home / ".claude" / ".mcp.json", {"mcpServers": {"engram": _entry({"API_TOKEN": _SECRET})}})
    M.register(lambda env: _entry({k: v for k, v in env.items() if k == "API_TOKEN"}))
    assert f"API_TOKEN={_SECRET}" in cli.calls[0]


def test_different_entry_is_kept_without_asking(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "old-python", "args": ["-m", "piia_engram.mcp_server"]}}})
    result = M.register(_build, on_differ="keep")
    assert result.status == "manual" and result.detail == "differs"
    assert cli.calls == []


def test_different_entry_replaced_after_confirm(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "old-python", "args": ["-m", "piia_engram.mcp_server"]}}})
    result = M.register(_build, on_differ="ask", confirm_replace=lambda: True)
    assert result.status == "replaced"
    assert cli.calls == [["/fake/bin/claude", "mcp", "remove", "--scope", "user", "engram"], EXPECTED_ADD]


def test_different_entry_declined_is_kept(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "old-python", "args": ["-m", "piia_engram.mcp_server"]}}})
    result = M.register(_build, on_differ="ask", confirm_replace=lambda: False)
    assert result.status == "kept"
    assert cli.calls == []


def test_add_failure_is_reported(home, monkeypatch):
    rec = Recorder({"add": 1})
    monkeypatch.setattr(M, "cli_path", lambda: "/fake/bin/claude")
    monkeypatch.setattr(M, "run_cli", rec)
    result = M.register(_build)
    assert result.status == "failed" and result.detail == "boom"
    assert result.command.startswith("claude mcp add")


def test_undetermined_user_config_asks_claude_mcp_get(home, cli, monkeypatch):
    _write(home / ".claude.json", {"mcpServers": {}})
    monkeypatch.setattr(M, "MAX_USER_CONFIG_BYTES", 2)
    result = M.register(_build)
    assert result.status == "present"
    assert cli.calls == [["/fake/bin/claude", "mcp", "get", "engram"]]


def test_registration_uses_claude_config_dir(home, config_dir, cli):
    # An entry in the home directory's file does not count once CLAUDE_CONFIG_DIR is set.
    _write(home / ".claude.json", {"mcpServers": {"engram": _build({})}})
    assert M.register(_build).status == "added"
    cli.calls.clear()
    _write(config_dir / ".claude.json", {"mcpServers": {"engram": _build({})}})
    assert M.register(_build).status == "unchanged"
    assert cli.calls == []


def test_fake_claude_executable_on_a_temporary_path(home, tmp_path, monkeypatch):
    """The real seams, pointed at a fake `claude` that only logs its arguments."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    log = tmp_path / "claude-calls.log"
    if os.name == "nt":
        script = bin_dir / "claude.cmd"
        script.write_text(f'@echo off\r\necho %*>>"{log}"\r\nexit /b 0\r\n', encoding="utf-8")
    else:
        script = bin_dir / "claude"
        script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n', encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(M, "cli_path", M._find_cli)
    monkeypatch.setattr(M, "run_cli", M._run)
    assert Path(M.cli_path()).parent == bin_dir

    assert M.register(_build).status == "added"
    logged = log.read_text(encoding="utf-8", errors="replace")
    assert "mcp add --scope user engram -e PYTHONIOENCODING=utf-8" in logged
    assert "piia_engram.mcp_server" in logged
    assert not (home / ".claude.json").exists()


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,entry", [
    ("engram", {"command": "python"}),
    ("piia-engram", {"command": "anything"}),
    ("memory", {"command": "C:\\Tools\\piia-engram-mcp.exe"}),
    ("memory", {"command": "uvx", "args": ["--from", "piia-engram", "piia-engram-mcp"]}),
    ("memory", {"command": "python", "args": ["-m", "piia_engram.mcp_server"]}),
])
def test_engram_entries_are_recognised(name, entry):
    assert M.is_engram_entry(name, entry)


def test_other_servers_are_not_engram():
    assert not M.is_engram_entry("other", {"command": "npx", "args": ["some-server"]})


def _doctor_text(fix: bool = False) -> str:
    buf = io.StringIO()
    saved = sys.modules.pop("piia_engram.mcp_server", None)
    import piia_engram

    try:
        with redirect_stdout(buf):
            doctor.run_doctor(fix=fix)
    finally:
        sys.modules.pop("piia_engram.mcp_server", None)
        if saved is not None:
            sys.modules["piia_engram.mcp_server"] = saved
            piia_engram.mcp_server = saved
    return buf.getvalue()


def _claude_tool() -> dict | None:
    return next((t for t in doctor._detect_installed_tools() if t["tool_id"] == "claude_code"), None)


def _claude_row(store: Path) -> dict:
    return next(r for r in C.build_report(store, days=14)["clients"] if r["tool_id"] == "claude_code")


LAYOUTS = {
    "engram_key": ({"mcpServers": {"engram": {"command": "x", "env": {"T": _SECRET}}}}, None, "configured"),
    "piia_engram_key": ({"mcpServers": {"piia-engram": {"command": "x"}}}, None, "configured"),
    "command_in_project": ({"projects": {"/p": {"mcpServers": {"mem": {"command": "piia-engram-mcp"}}}}},
                           None, "configured"),
    "legacy_only": ({"mcpServers": {}}, {"mcpServers": {"engram": _entry()}}, "legacy_only"),
    "nothing": ({"mcpServers": {"other": {"command": _SECRET}}}, None, "not_configured"),
}


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
@pytest.mark.parametrize("use_config_dir", [False, True])
def test_doctor_sections_agree(home, tmp_path, monkeypatch, layout, use_config_dir):
    from piia_engram.core import Engram

    store = tmp_path / "store"
    Engram(root=store)
    user_config, legacy, expected = LAYOUTS[layout]
    if use_config_dir:
        cdir = tmp_path / "claude-config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cdir))
        _write(cdir / ".claude.json", user_config)
        # The home directory's file must be ignored when CLAUDE_CONFIG_DIR is set.
        _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "x"}}})
    else:
        _write(home / ".claude.json", user_config)
    if legacy:
        _write(home / ".claude" / ".mcp.json", legacy)

    tool = _claude_tool()
    row = _claude_row(store)
    out = _doctor_text()
    claude_line = next(line for line in out.splitlines() if "Claude Code —" in line)
    assert _SECRET not in out
    if expected == "configured":
        assert tool["status"] == "configured"
        assert row["config_status"] == "configured"
        assert "[ok] Claude Code — Engram configured" in claude_line
    elif expected == "legacy_only":
        assert tool["status"] == "legacy"
        assert row["config_status"] == "legacy_only"
        assert "~/.claude/.mcp.json" in claude_line and "does not read" in claude_line
        assert "engram setup" in out
        assert "does not read" in "\n".join(C.render_text(C.build_report(store, days=14)))
    else:
        assert tool["status"] == "installed"
        assert row["config_status"] == "not_configured"
        assert "Engram NOT configured" in claude_line


def test_legacy_entry_never_validated_or_rewritten(home, tmp_path):
    from piia_engram.core import Engram

    Engram(root=tmp_path / "store")
    legacy = _write(home / ".claude" / ".mcp.json",
                    {"mcpServers": {"engram": {"command": "/missing/python", "args": ["-m", "x"]}}})
    tool = _claude_tool()
    assert tool["detect_only"] is True and tool["servers"] == {}
    before = legacy.read_bytes()
    _doctor_text(fix=True)
    assert legacy.read_bytes() == before


def test_no_claude_code_files_means_not_installed(home, tmp_path):
    assert _claude_tool() is None
    assert _claude_row(tmp_path / "store")["config_status"] == "not_installed"


# ---------------------------------------------------------------------------
# doctor --fix
# ---------------------------------------------------------------------------


def _legacy_world(home: Path, tmp_path: Path) -> tuple[Path, Path]:
    from piia_engram.core import Engram

    Engram(root=tmp_path / "store")
    user = _write(home / ".claude.json", {"mcpServers": {"other": {"command": "x"}}})
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {
        "engram": _entry({"API_TOKEN": _SECRET}), "keep-me": {"command": "y"}}})
    return user, legacy


def test_doctor_fix_registers_through_the_cli_and_leaves_both_files(home, tmp_path, cli):
    user, legacy = _legacy_world(home, tmp_path)
    before_user, before_legacy = user.read_bytes(), legacy.read_bytes()

    out = _doctor_text(fix=True)

    assert len(cli.calls) == 1 and cli.calls[0][1:6] == ["mcp", "add", "--scope", "user", "engram"]
    assert f"API_TOKEN={_SECRET}" in cli.calls[0]  # the owner's key carries over
    assert user.read_bytes() == before_user
    assert legacy.read_bytes() == before_legacy
    assert "~/.claude/.mcp.json" in out
    assert _SECRET not in out


def test_doctor_fix_without_cli_prints_the_command_and_writes_nothing(home, tmp_path):
    user, legacy = _legacy_world(home, tmp_path)
    before_user, before_legacy = user.read_bytes(), legacy.read_bytes()
    out = _doctor_text(fix=True)
    assert "claude mcp add --scope user engram" in out
    assert "API_TOKEN" in out and _SECRET not in out
    # (--fix still refreshes the CLAUDE.md snippet and hooks, as before.)
    assert user.read_bytes() == before_user
    assert legacy.read_bytes() == before_legacy


def test_doctor_without_fix_runs_no_command(home, tmp_path, cli):
    _legacy_world(home, tmp_path)
    out = _doctor_text(fix=False)
    assert cli.calls == []
    assert "engram doctor --fix" in out or "engram setup" in out


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------


def _claude_setup_tool() -> dict:
    return next(t for t in W._detect_tools() if t["id"] == "claude_code")


def _answers(monkeypatch, *replies: str) -> list[str]:
    asked: list[str] = []
    queue = list(replies)

    def fake_prompt(message, default=""):
        asked.append(message)
        return queue.pop(0) if queue else default

    monkeypatch.setattr(W, "_prompt", fake_prompt)
    return asked


def _apply(tmp_path: Path, *, interactive: bool):
    tool = _claude_setup_tool()
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = W._apply_external_configs(
            [tool], PY, "piia_engram.mcp_server", str(tmp_path / "store"), interactive=interactive)
    return result, buf.getvalue()


def test_setup_detects_claude_code_from_its_config_dir(home):
    (home / ".claude").mkdir()
    tool = _claude_setup_tool()
    assert tool["register_via"] == "claude_cli"
    assert tool["config_path"] == home / ".claude.json"


def test_setup_registers_through_the_cli(home, tmp_path, cli, monkeypatch):
    (home / ".claude").mkdir()
    _answers(monkeypatch)
    (success, failed, manual), out = _apply(tmp_path, interactive=True)
    assert success == ["Claude Code"] and failed == [] and manual == []
    assert len(cli.calls) == 1
    argv = cli.calls[0]
    assert argv[1:6] == ["mcp", "add", "--scope", "user", "engram"]
    assert argv[argv.index("--") + 1:] == [PY, "-m", "piia_engram.mcp_server"]
    assert f"ENGRAM_DIR={tmp_path / 'store'}" in argv
    assert "ENGRAM_TOOLS=all" in argv
    assert not (home / ".claude.json").exists()
    assert not (home / ".claude" / ".mcp.json").exists()


def test_setup_rerun_with_the_same_entry_does_not_add(home, tmp_path, cli, monkeypatch):
    (home / ".claude").mkdir()
    _answers(monkeypatch)
    _apply(tmp_path, interactive=False)
    argv = cli.calls[0]
    sep = argv.index("--")
    env = dict(a.split("=", 1) for a in argv[6:sep] if a != "-e")
    _write(home / ".claude.json", {"mcpServers": {"engram": {
        "type": "stdio", "command": argv[sep + 1], "args": argv[sep + 2:], "env": env}}})
    cli.calls.clear()
    (success, _, _), _ = _apply(tmp_path, interactive=False)
    assert success == ["Claude Code"] and cli.calls == []


def test_setup_without_cli_marks_manual_and_writes_no_config(home, tmp_path, monkeypatch):
    (home / ".claude").mkdir()
    _answers(monkeypatch)
    (success, failed, manual), out = _apply(tmp_path, interactive=True)
    assert manual == ["Claude Code"] and success == [] and failed == []
    assert "claude mcp add --scope user engram" in out
    assert not (home / ".claude.json").exists()
    assert not (home / ".claude" / ".mcp.json").exists()


def test_setup_manual_hint_mentions_claude_config_dir(home, config_dir, tmp_path, monkeypatch):
    _answers(monkeypatch)
    (_, _, manual), out = _apply(tmp_path, interactive=True)
    assert manual == ["Claude Code"]
    assert "CLAUDE_CONFIG_DIR" in out
    assert not (config_dir / ".claude.json").exists()


def test_setup_removes_only_the_old_engram_entry_after_confirm(home, tmp_path, cli, monkeypatch):
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {
        "engram": _entry(), "piia-engram": {"command": "piia-engram-mcp"},
        "keep-me": {"command": "y"}}, "extra": 1})
    asked = _answers(monkeypatch, "1")
    (success, _, _), out = _apply(tmp_path, interactive=True)
    assert success == ["Claude Code"]
    assert "does not read" in out or "不会读取" in out
    assert any(".mcp.json" in q for q in asked)
    data = json.loads(legacy.read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"keep-me": {"command": "y"}}, "extra": 1}


def test_setup_keeps_the_old_entry_when_declined(home, tmp_path, cli, monkeypatch):
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {"engram": _entry(), "keep-me": {}}})
    before = legacy.read_bytes()
    _answers(monkeypatch, "2")
    _apply(tmp_path, interactive=True)
    assert legacy.read_bytes() == before


def test_setup_non_interactive_never_removes_the_old_entry(home, tmp_path, cli, monkeypatch):
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {"engram": _entry()}})
    before = legacy.read_bytes()
    asked = _answers(monkeypatch)
    _, out = _apply(tmp_path, interactive=False)
    assert legacy.read_bytes() == before
    assert asked == []
    assert ".mcp.json" in out


def test_setup_does_not_offer_cleanup_when_registration_is_manual(home, tmp_path, monkeypatch):
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {"engram": _entry()}})
    before = legacy.read_bytes()
    asked = _answers(monkeypatch)
    _apply(tmp_path, interactive=True)
    assert legacy.read_bytes() == before
    assert not any(".mcp.json" in q for q in asked)


def test_setup_asks_before_replacing_a_different_entry(home, tmp_path, cli, monkeypatch):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "old-python", "args": ["-m", "piia_engram.mcp_server"]}}})
    asked = _answers(monkeypatch, "2")
    (success, _, manual), _ = _apply(tmp_path, interactive=True)
    assert cli.calls == [] and success == ["Claude Code"] and manual == []
    assert asked
    asked = _answers(monkeypatch, "1")
    _apply(tmp_path, interactive=True)
    assert [c[2] for c in cli.calls] == ["remove", "add"]


def test_setup_non_interactive_does_not_replace_a_different_entry(home, tmp_path, cli, monkeypatch):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "old-python", "args": ["-m", "piia_engram.mcp_server"]}}})
    _answers(monkeypatch)
    (success, _, manual), out = _apply(tmp_path, interactive=False)
    assert cli.calls == [] and manual == ["Claude Code"]
    assert "claude mcp remove --scope user engram" in out


def test_write_tool_mcp_config_refuses_claude_code(home, tmp_path):
    (home / ".claude").mkdir()
    with pytest.raises(ValueError):
        W._write_tool_mcp_config(_claude_setup_tool(), PY, "piia_engram.mcp_server",
                                 str(tmp_path / "store"))
    assert not (home / ".claude.json").exists()


# ---------------------------------------------------------------------------
# other readers: status, dock governance, integrity report, MCP start-up check
# ---------------------------------------------------------------------------

READER_LAYOUTS = {
    # name: (user config, old file, detection status)
    "engram_key": ({"mcpServers": {"engram": {"command": "x", "env": {"T": _SECRET}}}}, None, "configured"),
    "piia_engram_key": ({"mcpServers": {"piia-engram": {"command": "x"}}}, None, "configured"),
    "command_in_project": ({"projects": {"/p": {"mcpServers": {"mem": {"command": "piia-engram-mcp"}}}}},
                           None, "configured"),
    "legacy_only": ({"mcpServers": {}}, {"mcpServers": {"engram": _entry({"T": _SECRET})}}, "legacy_only"),
    "nothing": ({"mcpServers": {"other": {"command": _SECRET}}}, None, "not_configured"),
    "too_large": ({"mcpServers": {"engram": {"command": "x"}}}, None, "undetermined"),
}
STATUS_ROWS = {
    "configured": ("configured", "claude_cli"),
    "undetermined": ("needs attention", "unknown"),
    "legacy_only": ("needs attention", "legacy_only"),
    "not_configured": ("missing entry", "missing"),
}


def _reader_layout(home: Path, monkeypatch, layout: str, use_config_dir: bool, tmp_path: Path) -> str:
    user_config, legacy, expected = READER_LAYOUTS[layout]
    if use_config_dir:
        cdir = tmp_path / "claude-config"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cdir))
        _write(cdir / ".claude.json", user_config)
        _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "x"}}})  # ignored
    else:
        _write(home / ".claude.json", user_config)
    if legacy:
        _write(home / ".claude" / ".mcp.json", legacy)
    if layout == "too_large":
        monkeypatch.setattr(M, "MAX_USER_CONFIG_BYTES", 2)
    return expected


@pytest.mark.parametrize("layout", sorted(READER_LAYOUTS))
@pytest.mark.parametrize("use_config_dir", [False, True])
def test_status_summary_uses_the_shared_detection(home, tmp_path, monkeypatch, layout, use_config_dir):
    from piia_engram.status_report import _client_summary

    expected = _reader_layout(home, monkeypatch, layout, use_config_dir, tmp_path)
    summary = _client_summary()
    row = next(r for r in summary["tools"] if r["name"] == "Claude Code")
    assert (row["status"], row["style"]) == STATUS_ROWS[expected]
    dumped = json.dumps(summary)
    assert _SECRET not in dumped and str(home) not in dumped and str(tmp_path) not in dumped


def test_status_summary_claude_code_not_installed(home):
    from piia_engram.status_report import _client_summary

    row = next(r for r in _client_summary()["tools"] if r["name"] == "Claude Code")
    assert row["status"] == "not configured"


@pytest.mark.parametrize("layout", sorted(READER_LAYOUTS))
def test_dock_governance_uses_the_shared_detection(home, tmp_path, monkeypatch, layout):
    from piia_engram import cli_commands

    expected = _reader_layout(home, monkeypatch, layout, False, tmp_path)
    summary = cli_commands._dock_config_governance_summary()
    row = next(r for r in summary["clients"] if r["name"] == "Claude Code")
    assert row["status"] == STATUS_ROWS[expected][0]
    # The entry is not read for its env, so governance coverage is not known.
    assert row["governance_env"] == ("unknown" if expected in ("configured", "undetermined") else "missing")
    assert row.get("legacy_only", False) is (expected == "legacy_only")
    dumped = json.dumps(summary)
    assert _SECRET not in dumped and str(home) not in dumped


@pytest.mark.parametrize("layout", sorted(READER_LAYOUTS))
def test_integrity_report_uses_the_shared_detection(home, tmp_path, monkeypatch, layout):
    expected = _reader_layout(home, monkeypatch, layout, False, tmp_path)
    report = doctor._build_config_integrity_report(cwd=tmp_path)
    rows = [r for r in report["mcp_configs"] if r["tool_id"] == "claude_code"]
    assert len(rows) == 1
    row = rows[0]
    assert row["path"] == str(home / ".claude.json")
    assert row["configured"] is (expected == "configured")
    assert row["detection"] == expected
    assert row["legacy_entry_present"] is (layout == "legacy_only")
    assert "parent_exists" not in row
    assert row["config_dir_exists"] is (layout == "legacy_only")  # only that layout has ~/.claude/
    assert row["sha256_12"] == ""  # the user config is not hashed or read for content
    assert row["legacy_servers"] == []
    assert _SECRET not in json.dumps(report)


def test_integrity_report_follows_claude_config_dir(home, config_dir, tmp_path):
    _write(config_dir / ".claude.json", {"mcpServers": {"engram": {"command": "x"}}})
    report = doctor._build_config_integrity_report(cwd=tmp_path)
    row = next(r for r in report["mcp_configs"] if r["tool_id"] == "claude_code")
    assert row["path"] == str(config_dir / ".claude.json") and row["configured"] is True


def _fresh_auto_migrate(tmp_path: Path) -> str:
    store = tmp_path / "store"
    W.auto_migrate()
    log = store / "migration.log"
    return log.read_text(encoding="utf-8") if log.is_file() else ""


@pytest.mark.parametrize("layout", sorted(READER_LAYOUTS))
def test_mcp_startup_check_uses_the_shared_detection(home, tmp_path, monkeypatch, layout):
    _reader_layout(home, monkeypatch, layout, False, tmp_path)
    user_config = home / ".claude.json"
    before = user_config.read_bytes()
    legacy = home / ".claude" / ".mcp.json"
    legacy_before = legacy.read_bytes() if legacy.is_file() else None

    log = _fresh_auto_migrate(tmp_path)

    assert user_config.read_bytes() == before
    assert (legacy.read_bytes() if legacy.is_file() else None) == legacy_before
    assert _SECRET not in log
    if layout == "legacy_only":
        assert "~/.claude/.mcp.json" in log and "does not read" in log
    else:
        assert ".claude" not in log


# ---------------------------------------------------------------------------
# finding and running the claude command safely
# ---------------------------------------------------------------------------


def _fake_cli_file(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        script = directory / "claude.cmd"
        script.write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    else:
        script = directory / "claude"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        script.chmod(0o755)
    return script


def test_find_cli_ignores_the_current_directory_and_relative_path_entries(tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    _fake_cli_file(cwd)
    _fake_cli_file(cwd / "rel")
    empty_abs = tmp_path / "empty-bin"
    empty_abs.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "rel", str(empty_abs)]))
    assert M._find_cli() is None

    good = _fake_cli_file(tmp_path / "abs-bin")
    monkeypatch.setenv("PATH", os.pathsep.join([".", "rel", str(tmp_path / "abs-bin")]))
    found = M._find_cli()
    assert found is not None and Path(found).is_absolute()
    assert Path(found).resolve() == good.resolve()


@pytest.mark.skipif(os.name != "nt", reason="PATHEXT is a Windows lookup rule")
def test_find_cli_follows_pathext(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude.bat").write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("PATHEXT", ".EXE;.BAT")
    assert Path(M._find_cli()).name.lower() == "claude.bat"
    monkeypatch.setenv("PATHEXT", ".EXE")
    assert M._find_cli() is None


@pytest.mark.parametrize("bad", ["R&D", "100%", "a^b", 'q"uote', "x(86)", "a|b", "line\nbreak", "bang!"])
def test_a_batch_shim_with_unsafe_arguments_is_not_run(home, monkeypatch, bad):
    rec = Recorder()
    monkeypatch.setattr(M, "cli_path", lambda: "C:/tools/claude.cmd")
    monkeypatch.setattr(M, "run_cli", rec)
    result = M.register(lambda env: _entry({"ENGRAM_DIR": f"/data/{bad}"}))
    assert result.status == "manual" and result.detail == "cmd_unsafe"
    assert rec.calls == []
    assert result.command.startswith("claude mcp add")


def test_a_batch_shim_with_safe_arguments_runs(home, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(M, "cli_path", lambda: "C:/tools/claude.CMD")
    monkeypatch.setattr(M, "run_cli", rec)
    assert M.register(lambda env: _entry({"ENGRAM_DIR": "/data/with space"})).status == "added"
    assert len(rec.calls) == 1


def test_an_exe_is_run_with_any_argument(home, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(M, "cli_path", lambda: "C:/tools/claude.exe")
    monkeypatch.setattr(M, "run_cli", rec)
    assert M.register(lambda env: _entry({"ENGRAM_DIR": "/data/R&D 100%"})).status == "added"


@pytest.mark.skipif(os.name != "nt", reason="claude.cmd runs through cmd.exe on Windows only")
@pytest.mark.parametrize("data_dir,runs", [
    ("C:/Engram Data/store", True),
    ("C:/R&D/store", False),
    ("C:/100%/store", False),
    ("C:/a^b/store", False),
])
def test_fake_claude_cmd_regression(home, tmp_path, monkeypatch, data_dir, runs):
    bin_dir = tmp_path / "fake bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "claude.cmd").write_text(f'@echo off\r\necho %*>>"{log}"\r\nexit /b 0\r\n', encoding="utf-8")
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(M, "cli_path", M._find_cli)
    monkeypatch.setattr(M, "run_cli", M._run)

    result = M.register(lambda env: _entry({"ENGRAM_DIR": data_dir}))

    if runs:
        assert result.status == "added"
        assert f'"ENGRAM_DIR={data_dir}"' in log.read_text(encoding="utf-8", errors="replace")
    else:
        assert result.status == "manual" and result.detail == "cmd_unsafe"
        assert not log.exists()


def test_a_same_named_entry_that_is_not_engram_is_never_replaced(home, cli):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "npx", "args": ["other-server"]}}})
    asked = []
    result = M.register(_build, on_differ="ask", confirm_replace=lambda: asked.append(1) or True)
    assert result.status == "conflict" and not result.registered
    assert cli.calls == [] and asked == []


def test_setup_says_a_foreign_engram_entry_is_not_engram(home, tmp_path, cli, monkeypatch):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "npx", "args": ["other-server"]}}})
    asked = _answers(monkeypatch, "1")
    (success, failed, manual), out = _apply(tmp_path, interactive=True)
    assert cli.calls == [] and asked == []
    assert manual == ["Claude Code"] and success == []
    assert "is not Engram" in out or "不是 Engram" in out


def test_manual_command_for_powershell_quotes_and_keeps_the_separator():
    entry = {"command": "C:/Program Files/Python/python.exe", "args": ["-m", "piia_engram.mcp_server"],
             "env": {"ENGRAM_DIR": "C:/it's here"}}
    text, _ = M.manual_command(entry, windows=True)
    assert text.startswith("claude mcp add --scope user engram")
    assert "'C:/Program Files/Python/python.exe'" in text
    assert "'ENGRAM_DIR=C:/it''s here'" in text
    assert " '--' " in text


def test_manual_command_for_posix_shells():
    entry = {"command": "/opt/my py/python3", "args": ["-m", "piia_engram.mcp_server"],
             "env": {"ENGRAM_DIR": "/data/$HOME"}}
    text, _ = M.manual_command(entry, windows=False)
    assert "'/opt/my py/python3'" in text
    assert "'ENGRAM_DIR=/data/$HOME'" in text
    assert " -- " in text


# ---------------------------------------------------------------------------
# the old file: what may be removed, and how it is read
# ---------------------------------------------------------------------------


def test_only_entries_matching_name_and_command_are_removed(home, tmp_path, cli, monkeypatch):
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {
        "engram": _entry(),
        "piia-engram": {"command": "npx", "args": ["something-else"]},
        "memory": {"command": "piia-engram-mcp"},
        "keep-me": {"command": "y"}}})
    _answers(monkeypatch, "1")
    _, out = _apply(tmp_path, interactive=True)
    data = json.loads(legacy.read_text(encoding="utf-8"))
    assert sorted(data["mcpServers"]) == ["keep-me", "memory", "piia-engram"]
    assert "memory" in out and "piia-engram" in out  # named, left for the user


def test_nothing_removable_means_no_question(home, tmp_path, cli, monkeypatch):
    legacy = _write(home / ".claude" / ".mcp.json", {"mcpServers": {"memory": {"command": "piia-engram-mcp"}}})
    before = legacy.read_bytes()
    asked = _answers(monkeypatch, "1")
    _apply(tmp_path, interactive=True)
    assert legacy.read_bytes() == before
    assert not any(".mcp.json" in q for q in asked)


def test_removal_keeps_bom_and_indent(home, tmp_path, cli, monkeypatch):
    legacy = home / ".claude" / ".mcp.json"
    legacy.parent.mkdir(parents=True)
    payload = {"mcpServers": {"engram": _entry(), "keep-me": {"command": "y"}}}
    legacy.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, indent=4).encode("utf-8"))
    _answers(monkeypatch, "1")
    _apply(tmp_path, interactive=True)
    raw = legacy.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8")
    assert '\n    "mcpServers"' in text.replace("\r\n", "\n")
    assert json.loads(text) == {"mcpServers": {"keep-me": {"command": "y"}}}


def test_old_file_is_read_with_a_size_limit(home, monkeypatch):
    _write(home / ".claude" / ".mcp.json", {"mcpServers": {"engram": _entry()}})
    assert M.read_legacy().has_engram
    monkeypatch.setattr(M, "MAX_USER_CONFIG_BYTES", 10)
    state = M.read_legacy()
    assert state.too_large and not state.has_engram


def test_user_config_is_checked_by_bytes_read_not_only_stat(home, monkeypatch):
    path = _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "x"}}})
    monkeypatch.setattr(M, "MAX_USER_CONFIG_BYTES", 10)

    real_stat = Path.stat

    def small_stat(self, *a, **k):  # stat says small; the read must still catch it
        st = real_stat(self, *a, **k)
        if self == path:
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid,
                                   st.st_gid, 1, st.st_atime, st.st_mtime, st.st_ctime))
        return st

    monkeypatch.setattr(Path, "stat", small_stat)
    assert M.read_user_config().status == "undetermined"


# ---------------------------------------------------------------------------
# doctor: MCP Client Env for Claude Code (key names only)
# ---------------------------------------------------------------------------


def _env_tool():
    return _claude_tool()


def test_env_check_flags_a_missing_key_by_name(home):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "x", "env": {"OTHER": _SECRET}}}})
    findings = doctor._client_env_findings([_env_tool()], strict=True, user_env={})
    assert [(t["name"], m) for t, m in findings] == [("Claude Code", {"ENGRAM_APPROVAL": "strict"})]


def test_env_check_accepts_a_present_key_without_reading_its_value(home):
    _write(home / ".claude.json", {"mcpServers": {"engram": {"command": "x", "env": {"ENGRAM_APPROVAL": _SECRET}}}})
    assert doctor._client_env_findings([_env_tool()], strict=True, user_env={}) == []
    tool = _env_tool()
    assert tool["env_keys"] == ["ENGRAM_APPROVAL"]
    assert _SECRET not in json.dumps(tool, default=str)


def test_env_check_watched_variable_by_key(home):
    _write(home / ".claude.json", {"mcpServers": {"piia-engram": {"command": "x", "env": {}}}})
    findings = doctor._client_env_findings([_env_tool()], strict=False,
                                           user_env={"ENGRAM_REVIEW_QUEUE_MAX": "7"})
    assert [m for _, m in findings] == [{"ENGRAM_REVIEW_QUEUE_MAX": "7"}]


def test_env_not_checked_for_a_project_only_entry(home, capsys):
    _write(home / ".claude.json", {"projects": {"/p": {"mcpServers": {"engram": {"command": "x"}}}}})
    findings = doctor._client_env_findings([_env_tool()], strict=True, user_env={})
    assert [(t["name"], m) for t, m in findings] == [("Claude Code", None)]
    doctor._print_client_env_findings(findings)
    out = capsys.readouterr().out
    assert "Claude Code: env not checked" in out
    assert "reach every configured client" not in out


# ---------------------------------------------------------------------------
# CLAUDE.md block and hooks follow CLAUDE_CONFIG_DIR
# ---------------------------------------------------------------------------


def test_instructions_and_hooks_go_to_claude_config_dir(home, config_dir, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    snippet = W._inject_instruction_snippet("claude_code", "en", file_safety_root=store,
                                            authorized_external_write=True)
    hook = W._inject_claude_code_hook(PY, file_safety_root=store, authorized_external_write=True)
    assert snippet and Path(snippet) == config_dir / "CLAUDE.md"
    assert hook and Path(hook) == config_dir / "settings.json"
    assert (config_dir / "CLAUDE.md").is_file() and (config_dir / "settings.json").is_file()
    assert not (home / ".claude").exists()
    rows = doctor._claude_hook_rows(Path.home())
    assert all(Path(r["settings_path"]) == config_dir / "settings.json" for r in rows)
    assert any(r["registered"] for r in rows)


def test_instructions_and_hooks_stay_in_home_without_claude_config_dir(home, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    snippet = W._inject_instruction_snippet("claude_code", "en", file_safety_root=store,
                                            authorized_external_write=True)
    hook = W._inject_claude_code_hook(PY, file_safety_root=store, authorized_external_write=True)
    assert Path(snippet) == home / ".claude" / "CLAUDE.md"
    assert Path(hook) == home / ".claude" / "settings.json"
