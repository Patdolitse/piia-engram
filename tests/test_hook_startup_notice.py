"""What the MCP server start reports about the registered Claude Code hooks.

Besides a hook Git Bash cannot run, the start now also reports a hook that
would open another store than the server itself (the server's ``ENGRAM_DIR``
is the one the MCP entry passes in), and a hook with a quoted interpreter
path on a machine without Git Bash, where nothing can start it. The check
stays read-only, runs no ``claude`` command and says nothing when it fails.

Every ``settings.json`` here is synthetic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import claude_code_mcp

PY = "/usr/bin/python3"
STOP = "piia_engram.hooks.auto_save_on_stop"
SS = "piia_engram.hooks.auto_inject_resume_brief"
QUOTED = f'"C:/Program Files/Python312/python.exe" -m {STOP}'


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def settings(tmp_path, monkeypatch):
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    path = config / "settings.json"

    def write(**events) -> Path:
        path.write_text(json.dumps({"hooks": {
            event: [{"hooks": [hook]}] for event, hook in events.items()}}), encoding="utf-8")
        return path

    return write


@pytest.fixture
def no_claude(monkeypatch):
    """A ``claude`` is on PATH; the check must not start it."""
    calls: list = []

    def run(argv, timeout=None):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="2.1.302", stderr="")

    monkeypatch.setattr(claude_code_mcp, "cli_path", lambda: r"C:\fake\claude.exe")
    monkeypatch.setattr(claude_code_mcp, "run_cli", run)
    return calls


def _string_hook(module: str, *env: str) -> dict:
    tail = "".join(f" --env {pair}" for pair in env)
    return {"type": "command", "command": f"{PY} -m {module}{tail}"}


class TestDataDirectory:
    def test_hook_that_does_not_name_the_servers_store(self, home, settings, tmp_path, no_claude):
        custom = tmp_path / "store"
        custom.mkdir()
        named = f"ENGRAM_DIR={custom.resolve().as_posix()}"
        path = settings(
            Stop=_string_hook(STOP),
            SessionStart=_string_hook(SS, "CLAUDE_INVOKED_BY=engram_session_start", named),
            PostCompact={"type": "command", "command": PY, "args": [
                "-m", "piia_engram.hooks.auto_absorb_compact", "--env", "ENGRAM_DIR=/elsewhere"]},
        )
        before = path.read_bytes()

        assert W.claude_hook_problems(data_dir=str(custom)) == ["Stop", "PostCompact"]
        assert W.claude_hook_problems() == []  # no store given: not judged
        assert path.read_bytes() == before
        assert no_claude == []

    def test_default_store_needs_no_name(self, home, settings):
        (home / ".engram").mkdir()
        settings(Stop=_string_hook(STOP))
        assert W.claude_hook_problems(data_dir=str(home / ".engram")) == []

    def test_owner_script_is_not_judged(self, home, settings, tmp_path):
        settings(Stop={"type": "command", "command": "my-wrapper.sh auto_save_on_stop"})
        assert W.claude_hook_problems(data_dir=str(tmp_path / "store")) == []


class TestQuotedInterpreter:
    def test_reported_only_where_no_git_bash_is_found(self, home, settings, monkeypatch, no_claude):
        settings(Stop={"type": "command", "command": QUOTED})

        monkeypatch.setattr(W, "_git_bash_found", lambda: False)
        assert W.claude_hook_problems(windows=True) == ["Stop"]

        monkeypatch.setattr(W, "_git_bash_found", lambda: True)
        assert W.claude_hook_problems(windows=True) == []  # Git Bash runs it
        assert no_claude == []

    def test_other_forms_and_platforms(self, home, settings, monkeypatch):
        monkeypatch.setattr(W, "_git_bash_found", lambda: False)
        settings(Stop={"type": "command", "command": QUOTED})
        assert W.claude_hook_problems(windows=False) == []
        settings(Stop={"type": "command", "command": r"C:\Program Files\Python312\python.exe",
                       "args": ["-m", STOP]})
        assert W.claude_hook_problems(windows=True) == []
        settings(Stop={"type": "command", "command": f"C:/PROGRA~1/Python312/python.exe -m {STOP}"})
        assert W.claude_hook_problems(windows=True) == []

    def test_a_failing_git_bash_lookup_reports_nothing(self, home, settings, monkeypatch):
        def boom():
            raise OSError("no PATH")

        monkeypatch.setattr(W, "_git_bash_found", boom)
        settings(Stop={"type": "command", "command": QUOTED})
        assert W.claude_hook_problems(windows=True) == []


class TestGitBashLookup:
    @pytest.fixture
    def bare_machine(self, tmp_path, monkeypatch):
        for name in ("CLAUDE_CODE_GIT_BASH_PATH", "ProgramFiles", "ProgramFiles(x86)",
                     "ProgramW6432", "LOCALAPPDATA"):
            # setenv first: delenv records nothing for a variable that is not set.
            monkeypatch.setenv(name, "recorded-for-restore")
            monkeypatch.delenv(name)
        monkeypatch.setattr(W.shutil, "which", lambda name: None)
        return tmp_path

    def test_nothing_installed(self, bare_machine):
        assert W._git_bash_found() is False

    def test_claude_codes_own_setting(self, bare_machine, monkeypatch):
        bash = bare_machine / "portable" / "bash.exe"
        bash.parent.mkdir()
        bash.write_bytes(b"")
        monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(bash))
        assert W._git_bash_found() is True
        monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(bare_machine / "missing.exe"))
        assert W._git_bash_found() is False

    def test_next_to_git_on_path(self, bare_machine, monkeypatch):
        root = bare_machine / "Git"
        (root / "cmd").mkdir(parents=True)
        (root / "bin").mkdir()
        (root / "cmd" / "git.exe").write_bytes(b"")
        (root / "bin" / "bash.exe").write_bytes(b"")
        monkeypatch.setattr(
            W.shutil, "which", lambda name: str(root / "cmd" / "git.exe") if name == "git" else None)
        assert W._git_bash_found() is True

    def test_default_install_location(self, bare_machine, monkeypatch):
        bash = bare_machine / "Program Files" / "Git" / "bin" / "bash.exe"
        bash.parent.mkdir(parents=True)
        bash.write_bytes(b"")
        monkeypatch.setenv("ProgramFiles", str(bare_machine / "Program Files"))
        assert W._git_bash_found() is True


class TestServerNotice:
    @pytest.fixture
    def server(self):
        from piia_engram import mcp_server
        return mcp_server

    def test_uses_the_servers_own_data_directory(self, server, home, settings, tmp_path, monkeypatch, no_claude):
        custom = tmp_path / "store"
        custom.mkdir()
        path = settings(SessionStart=_string_hook(SS, "CLAUDE_INVOKED_BY=engram_session_start"))
        before = path.read_bytes()
        monkeypatch.setenv("ENGRAM_DIR", str(custom))  # what the MCP entry passes in

        notice = server._claude_hook_notice()

        assert notice.startswith("[engram] notice:")
        assert "SessionStart" in notice and "engram doctor --fix" in notice
        assert path.read_bytes() == before and no_claude == []

    def test_no_directory_passed_in_means_no_data_directory_notice(self, server, home, settings, monkeypatch):
        settings(SessionStart=_string_hook(SS, "CLAUDE_INVOKED_BY=engram_session_start"))
        monkeypatch.setenv("ENGRAM_DIR", "recorded-for-restore")
        monkeypatch.delenv("ENGRAM_DIR")

        assert server._claude_hook_notice() == ""

    def test_hook_that_names_the_store_is_quiet(self, server, home, settings, tmp_path, monkeypatch):
        custom = tmp_path / "store"
        custom.mkdir()
        settings(SessionStart=_string_hook(
            SS, "CLAUDE_INVOKED_BY=engram_session_start",
            f"ENGRAM_DIR={custom.resolve().as_posix()}"))
        monkeypatch.setenv("ENGRAM_DIR", str(custom))

        assert server._claude_hook_notice() == ""
