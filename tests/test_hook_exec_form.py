"""Claude Code hooks registered as ``command`` + ``args`` on Windows.

A hook written as one ``command`` string is parsed by a shell: Git Bash on
Windows, or PowerShell when Git Bash is not installed. No single string
suits both once the interpreter path has a space: the quoted form Git Bash
needs is a string expression to PowerShell, so the hook never starts. With
an ``args`` list Claude Code starts the program itself and no shell parses
anything. Claude Code 2.1.139 added ``args``; Engram writes that form only
when the ``claude`` on PATH reports that version or a newer one, and keeps
the string form otherwise.

No real ``claude`` runs here: the two seams in ``claude_code_mcp`` are
replaced. Every ``settings.json`` is synthetic.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import claude_code_mcp, doctor

STOP = "piia_engram.hooks.auto_save_on_stop"
WIN_PY = r"C:\Program Files\Python312\python.exe"
SHELL_COMMAND = f'"C:/Program Files/Python312/python.exe" -m {STOP}'
FAKE_CLI = r"C:\fake\bin\claude.exe"


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def claude_version(monkeypatch):
    """Make the ``claude`` on PATH report a version; returns the call log."""
    calls: list[list[str]] = []

    def install(output: str | None, *, returncode: int = 0, raises: bool = False):
        monkeypatch.setattr(claude_code_mcp, "_VERSION_CACHE", {})
        monkeypatch.setattr(claude_code_mcp, "cli_path",
                            lambda: None if output is None else FAKE_CLI)

        def run(argv, timeout=None):
            calls.append(list(argv))
            if raises:
                raise OSError("cannot start")
            return subprocess.CompletedProcess(argv, returncode, stdout=output, stderr="")

        monkeypatch.setattr(claude_code_mcp, "run_cli", run)
        return calls

    return install


def _read(config: Path) -> dict:
    return json.loads((config / "settings.json").read_text(encoding="utf-8"))


def _write(config: Path, settings: dict) -> None:
    (config / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _hooks(settings: dict, event: str = "Stop") -> list[dict]:
    return [hook for group in settings.get("hooks", {}).get(event, [])
            for hook in group.get("hooks", [])]


def _inject(python_path: str = WIN_PY, **overrides) -> str | None:
    kwargs = {"event": "Stop", "module": STOP, "status_message": "s", "windows": True}
    kwargs.update(overrides)
    return W._inject_claude_code_hook_for_event(python_path, **kwargs)


class TestVersionGate:
    @pytest.mark.parametrize("output, expected", [
        ("2.1.139 (Claude Code)\n", True),
        ("2.1.302 (Claude Code)\n", True),
        ("3.0.0 (Claude Code)\n", True),
        ("2.1.138 (Claude Code)\n", False),
        ("1.9.999\n", False),
        ("claude: unknown\n", False),
        ("", False),
    ])
    def test_needs_the_version_that_added_args(self, claude_version, output, expected):
        claude_version(output)
        assert W._claude_code_exec_hooks(windows=True) is expected

    def test_no_claude_on_path_keeps_the_string_form(self, claude_version):
        calls = claude_version(None)
        assert W._claude_code_exec_hooks(windows=True) is False
        assert calls == []

    def test_a_failing_claude_keeps_the_string_form(self, claude_version):
        claude_version("2.1.302", returncode=1)
        assert W._claude_code_exec_hooks(windows=True) is False
        claude_version("2.1.302", raises=True)
        assert W._claude_code_exec_hooks(windows=True) is False

    def test_other_platforms_never_ask(self, claude_version):
        calls = claude_version("2.1.302")
        assert W._claude_code_exec_hooks(windows=False) is False
        assert calls == []

    def test_claude_is_asked_once(self, claude_version):
        calls = claude_version("2.1.302")
        assert W._claude_code_exec_hooks(windows=True)
        assert W._claude_code_exec_hooks(windows=True)
        assert calls == [[FAKE_CLI, "--version"]]


class TestRegistration:
    def test_new_hook_is_command_plus_args(self, claude_config, claude_version):
        claude_version("2.1.302 (Claude Code)")

        assert _inject(extra_env={"CLAUDE_INVOKED_BY": "engram_precompact"})

        (hook,) = _hooks(_read(claude_config))
        assert hook == {
            "type": "command",
            "command": WIN_PY,  # one program path: nothing to quote or escape
            "args": ["-m", STOP, "--env", "CLAUDE_INVOKED_BY=engram_precompact"],
            "timeout": 30,
            "statusMessage": "s",
            "async": True,
        }

    def test_old_claude_code_gets_the_string_form(self, claude_config, claude_version):
        claude_version("2.1.100 (Claude Code)")
        _inject()
        (hook,) = _hooks(_read(claude_config))
        assert hook["command"] == SHELL_COMMAND and "args" not in hook

    def test_other_platforms_get_the_string_form(self, claude_config, claude_version):
        claude_version("2.1.302 (Claude Code)")
        _inject("/usr/bin/python3", windows=False)
        (hook,) = _hooks(_read(claude_config))
        assert hook["command"] == f"/usr/bin/python3 -m {STOP}" and "args" not in hook

    def test_data_directory_is_one_argument(self, claude_config, claude_version, tmp_path, monkeypatch):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        custom = tmp_path / "My Data"
        custom.mkdir()
        claude_version("2.1.302 (Claude Code)")

        _inject(data_dir=custom)

        (hook,) = _hooks(_read(claude_config))
        assert hook["args"] == ["-m", STOP, "--env", f"ENGRAM_DIR={custom.resolve().as_posix()}"]
        assert not W._hook_lacks_data_dir(hook, W._hook_data_dir(custom))

    def test_setup_leaves_a_working_string_hook_alone(self, claude_config, claude_version):
        claude_version("2.1.302 (Claude Code)")
        written = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": SHELL_COMMAND}]}]}}
        _write(claude_config, written)

        assert _inject() is None
        assert _read(claude_config) == written

    def test_fix_converts_a_string_hook_in_place(self, claude_config, claude_version):
        claude_version("2.1.302 (Claude Code)")
        mine = {"type": "command", "command": "echo mine"}
        old = {"type": "command", "command": SHELL_COMMAND, "timeout": 5, "async": False}
        _write(claude_config, {"hooks": {"Stop": [{"matcher": "x", "hooks": [mine, old]}]}})

        assert _inject(force_rewrite=True)

        settings = _read(claude_config)
        assert settings["hooks"]["Stop"][0]["matcher"] == "x"
        first, hook = _hooks(settings)
        assert first == mine
        assert hook["command"] == WIN_PY and hook["args"] == ["-m", STOP]
        assert hook["async"] is False  # as found
        # Nothing left to do.
        path = claude_config / "settings.json"
        mtime = path.stat().st_mtime_ns
        assert _inject(force_rewrite=True) is None
        assert path.stat().st_mtime_ns == mtime

    def test_fix_converts_the_kept_duplicate(self, claude_config, claude_version):
        claude_version("2.1.302 (Claude Code)")
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": SHELL_COMMAND},
            {"type": "command", "command": SHELL_COMMAND, "timeout": 9},
        ]}]}})

        assert _inject(force_rewrite=True)

        (hook,) = _hooks(_read(claude_config))
        assert hook["command"] == WIN_PY and hook["args"] == ["-m", STOP]

    def test_fix_does_not_convert_without_a_new_enough_claude(self, claude_config, claude_version):
        claude_version("2.1.100 (Claude Code)")
        written = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": SHELL_COMMAND}]}]}}
        _write(claude_config, written)

        assert _inject(force_rewrite=True) is None
        assert _read(claude_config) == written

    def test_the_four_engram_hooks_are_recognized(self, claude_config, claude_version, monkeypatch):
        claude_version("2.1.302 (Claude Code)")
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        for injector in (W._inject_claude_code_hook, W._inject_claude_code_precompact_hook,
                         W._inject_claude_code_sessionstart_hook,
                         W._inject_claude_code_postcompact_hook):
            assert injector(WIN_PY)
            assert injector(WIN_PY) is None  # seen, not added twice

        settings = _read(claude_config)
        for event in ("Stop", "PreCompact", "SessionStart", "PostCompact"):
            (hook,) = _hooks(settings, event)
            assert hook["command"] == WIN_PY and hook["args"][0] == "-m"
            assert not W._hook_command_has_unquoted_backslash(hook, windows=True)
        rows = doctor._claude_hook_rows(Path.home())
        assert len(rows) == 4 and all(row["registered"] for row in rows)
        assert "async" not in _hooks(settings, "SessionStart")[0]


def test_the_argument_vector_starts_the_hook_without_a_shell(tmp_path):
    """What Claude Code does with ``command`` + ``args``: one program, one
    argument each, here with the running interpreter."""
    command, args = W._build_engram_hook_exec(
        sys.executable, module=STOP, extra_env={"CLAUDE_INVOKED_BY": "engram_precompact"})
    assert command == sys.executable

    done = subprocess.run([command, *args], input="{}", capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr


class TestDoctor:
    @pytest.fixture
    def run_doctor(self, tmp_path, monkeypatch, capsys, claude_config):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("ENGRAM_DIR", str(home / ".engram"))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        monkeypatch.setattr(W, "_find_python", lambda: WIN_PY)

        def run(*, fix: bool) -> str:
            capsys.readouterr()
            doctor._run_functional_checks(fix=fix)
            return capsys.readouterr().out

        return run

    def test_string_hook_is_pointed_out_then_converted(self, run_doctor, claude_config, claude_version):
        claude_version("2.1.302 (Claude Code)")
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": SHELL_COMMAND, "async": True}]}]}})

        out = run_doctor(fix=False)
        assert "[ok] Stop hook registered" in out
        assert "[--] Stop hook is one shell command" in out
        assert _hooks(_read(claude_config))[0]["command"] == SHELL_COMMAND  # read-only

        out = run_doctor(fix=True)
        assert "[fixed] Stop hook rewritten in place" in out
        (hook,) = _hooks(_read(claude_config))
        assert hook["command"] == WIN_PY and hook["args"] == ["-m", STOP]
        assert hook["async"] is True

        out = run_doctor(fix=False)
        assert "[ok] Stop hook registered" in out
        assert "is one shell command" not in out

    def test_old_claude_code_is_not_nagged(self, run_doctor, claude_config, claude_version):
        claude_version("2.1.100 (Claude Code)")
        written = {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": SHELL_COMMAND}]}]}}
        _write(claude_config, written)

        out = run_doctor(fix=True)

        assert "[ok] Stop hook registered" in out
        assert "is one shell command" not in out
        assert _read(claude_config)["hooks"]["Stop"] == written["hooks"]["Stop"]
