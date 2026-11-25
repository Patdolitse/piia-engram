"""A broken Claude Code hook is repaired by setup and announced at server start.

After an upgrade the hooks written by an older Engram stay as they were:
on Windows a bare backslash interpreter path that Git Bash cannot run.
Only ``engram doctor --fix`` rewrote it; re-running ``engram setup`` saw
"already registered" and left it, and nothing said so. Setup now repairs
such a hook in place, and the MCP server start reports it on stderr. The
server only reads: it never writes Claude Code's settings.

Every ``settings.json`` here is synthetic.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)

STOP = "piia_engram.hooks.auto_save_on_stop"
SS = "piia_engram.hooks.auto_inject_resume_brief"
WIN_PY = r"C:\Users\u\AppData\Local\Programs\Python\Python314\python.exe"
BARE = f"{WIN_PY} -m {STOP}"
RUNNABLE = WIN_PY.replace("\\", "/") + f" -m {STOP}"
MINE = {"type": "command", "command": "echo mine"}


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


def _read(config: Path) -> dict:
    return json.loads((config / "settings.json").read_text(encoding="utf-8"))


def _write(config: Path, settings) -> Path:
    path = config / "settings.json"
    path.write_text(settings if isinstance(settings, str) else json.dumps(settings, indent=2),
                    encoding="utf-8")
    return path


def _hooks(settings: dict, event: str = "Stop") -> list[dict]:
    return [hook for group in settings.get("hooks", {}).get(event, [])
            for hook in group.get("hooks", [])]


def _setup(**overrides) -> str | None:
    """The Stop hook as ``engram setup`` registers it: no ``force_rewrite``."""
    kwargs = {"event": "Stop", "module": STOP, "status_message": "s", "windows": True}
    kwargs.update(overrides)
    return W._inject_claude_code_hook_for_event(WIN_PY, **kwargs)


class TestSetupRepairs:
    def test_bare_backslash_hook_is_rewritten_in_place(self, claude_config):
        old = {"type": "command", "command": BARE, "timeout": 10, "async": False}
        _write(claude_config, {"hooks": {"Stop": [{"matcher": "m", "hooks": [dict(MINE), old]}]}})

        assert _setup() == str(claude_config / "settings.json")

        settings = _read(claude_config)
        assert settings["hooks"]["Stop"][0]["matcher"] == "m"
        first, hook = _hooks(settings)  # no second registration
        assert first == MINE
        assert hook["command"] == RUNNABLE
        assert hook["async"] is False  # as found
        assert not W._hook_command_has_unquoted_backslash(hook, windows=True)
        assert _setup() is None  # nothing left to do

    def test_duplicates_with_a_broken_hook_become_one(self, claude_config):
        _write(claude_config, {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": BARE}]},
            {"hooks": [{"type": "command", "command": BARE, "timeout": 3}, dict(MINE)]},
        ]}})

        assert _setup()

        hook, mine = _hooks(_read(claude_config))
        assert hook["command"] == RUNNABLE and mine == MINE

    def test_runnable_hook_is_left_alone(self, claude_config):
        written = {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": RUNNABLE, "timeout": 3}]}]}}
        path = _write(claude_config, written)
        mtime = path.stat().st_mtime_ns

        assert _setup() is None
        assert path.stat().st_mtime_ns == mtime

    def test_owner_wrapper_is_left_alone(self, claude_config):
        written = {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": f"my-wrapper.sh --module {STOP}"}]}]}}
        _write(claude_config, written)

        assert _setup() is None
        assert _read(claude_config) == written

    def test_other_platforms_do_not_judge_backslashes(self, claude_config):
        written = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": BARE}]}]}}
        _write(claude_config, written)

        assert _setup(windows=False) is None
        assert _read(claude_config) == written


class TestProblemCheck:
    def test_names_the_events_with_a_hook_git_bash_cannot_run(self, claude_config):
        path = _write(claude_config, {"hooks": {
            "Stop": [{"hooks": [dict(MINE), {"type": "command", "command": BARE}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": f"{WIN_PY} -m {SS}"}]}],
            "PostCompact": [{"hooks": [{"type": "command", "command": "C:\\tools\\other.exe"}]}],
        }})
        before = path.read_bytes()

        assert W.claude_hook_problems(windows=True) == ["Stop", "SessionStart"]
        assert W.claude_hook_problems(windows=False) == []
        assert path.read_bytes() == before  # read-only

    def test_healthy_forms_are_not_reported(self, claude_config):
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": RUNNABLE},
            {"type": "command", "command": WIN_PY, "args": ["-m", STOP]},
            {"type": "command", "command": f'"{WIN_PY}" -m {STOP}'},
        ]}]}})
        assert W.claude_hook_problems(windows=True) == []

    @pytest.mark.parametrize("content", ["{not json", "[]", '{"hooks": 3}', ""])
    def test_unreadable_settings_report_nothing(self, claude_config, content):
        _write(claude_config, content)
        assert W.claude_hook_problems(windows=True) == []

    def test_missing_settings_report_nothing(self, claude_config):
        assert W.claude_hook_problems(windows=True) == []

    def test_oversized_settings_are_not_parsed(self, claude_config, monkeypatch):
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": BARE}]}]}})
        monkeypatch.setattr(W, "_HOOK_CHECK_MAX_BYTES", 10)
        assert W.claude_hook_problems(windows=True) == []


class TestServerStart:
    @pytest.fixture
    def server(self):
        from piia_engram import mcp_server
        return mcp_server

    def test_notice_names_the_fix(self, server, monkeypatch):
        monkeypatch.setattr(W, "claude_hook_problems", lambda **kw: ["Stop", "SessionStart"])
        notice = server._claude_hook_notice()
        assert notice.startswith("[engram] notice:")
        assert "Stop, SessionStart" in notice and "engram doctor --fix" in notice

    def test_no_problem_no_notice(self, server, monkeypatch):
        monkeypatch.setattr(W, "claude_hook_problems", lambda **kw: [])
        assert server._claude_hook_notice() == ""

    def test_a_failing_check_is_silent(self, server, monkeypatch, capsys):
        def boom(**kw):
            raise RuntimeError("settings unreadable")

        monkeypatch.setattr(W, "claude_hook_problems", boom)
        assert server._claude_hook_notice() == ""
        thread = server._start_claude_hook_check()
        thread.join(timeout=10)
        captured = capsys.readouterr()
        assert captured.out == "" and captured.err == ""

    def test_check_goes_to_stderr_from_a_daemon_thread(self, server, claude_config, monkeypatch, capsys):
        path = _write(claude_config, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": BARE}]}]}})
        before = path.read_bytes()
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)

        thread = server._start_claude_hook_check()

        assert thread.daemon and thread.name == "engram-hook-check"
        thread.join(timeout=10)
        captured = capsys.readouterr()
        assert captured.out == ""  # stdout carries the MCP protocol
        assert "engram doctor --fix" in captured.err and "Stop" in captured.err
        assert path.read_bytes() == before  # the server never writes the file

    def test_main_starts_the_check_without_waiting_for_it(self, server, monkeypatch):
        events: list[str] = []
        gate = threading.Event()
        monkeypatch.setattr(server, "_parse_args", lambda: SimpleNamespace(
            transport="stdio", host="127.0.0.1", port=8123))
        monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
        monkeypatch.setattr(server, "_configure_utf8_stdio", lambda: None)
        monkeypatch.setattr(server, "_run_startup_auto_migrate", lambda: None)
        monkeypatch.setattr(server.mcp, "run", lambda transport: events.append(f"run:{transport}"))

        def slow_check(**kw):
            gate.wait(timeout=30)
            events.append("checked")
            return []

        monkeypatch.setattr(W, "claude_hook_problems", slow_check)

        server.main()

        assert events == ["run:stdio"]  # the server ran while the check was still busy
        gate.set()
        for thread in threading.enumerate():
            if thread.name == "engram-hook-check":
                thread.join(timeout=10)
        assert events == ["run:stdio", "checked"]

    def test_ephemeral_start_skips_the_check(self, server, monkeypatch):
        started: list[str] = []
        monkeypatch.setattr(server, "_parse_args", lambda: SimpleNamespace(
            transport="stdio", host="127.0.0.1", port=8123))
        monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
        monkeypatch.setattr(server, "_configure_utf8_stdio", lambda: None)
        monkeypatch.setattr(server.mcp, "run", lambda transport: None)
        monkeypatch.setattr(server, "_start_claude_hook_check", lambda: started.append("check"))

        server.main()

        assert started == []
