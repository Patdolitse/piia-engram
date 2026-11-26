"""``--hook-args``: the owner says that Claude Code knows ``command`` + ``args``.

Engram writes that form by itself only when the ``claude`` on PATH reports a
new enough version. The desktop app and the VS Code extension bring their own
Claude Code and put nothing on PATH, so there the owner can say it instead:
``engram doctor --fix --hook-args`` and ``engram setup --hook-args``.

No real ``claude`` runs. Every ``settings.json`` is synthetic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W
from piia_engram import claude_code_mcp, doctor

STOP = "piia_engram.hooks.auto_save_on_stop"
LONG = r"C:\Program Files\Python312\python.exe"
QUOTED_COMMAND = f'"C:/Program Files/Python312/python.exe" -m {STOP}'


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def claude_version(monkeypatch):
    def install(output: str | None):
        monkeypatch.setattr(claude_code_mcp, "_VERSION_CACHE", {})
        monkeypatch.setattr(claude_code_mcp, "cli_path",
                            lambda: None if output is None else r"C:\fake\claude.exe")
        monkeypatch.setattr(
            claude_code_mcp, "run_cli",
            lambda argv, timeout=None: subprocess.CompletedProcess(argv, 0, stdout=output, stderr=""))

    return install


def _hooks(config: Path) -> list[dict]:
    settings = json.loads((config / "settings.json").read_text(encoding="utf-8"))
    return [hook for group in settings["hooks"]["Stop"] for hook in group["hooks"]]


def _write(config: Path, *hooks: dict) -> Path:
    path = config / "settings.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": list(hooks)}]}}, indent=2),
                    encoding="utf-8")
    return path


def _inject(**overrides):
    kwargs = {"event": "Stop", "module": STOP, "status_message": "s", "windows": True}
    kwargs.update(overrides)
    return W._inject_claude_code_hook_for_event(LONG, **kwargs)


class TestForcedForm:
    def test_without_the_flag_no_claude_means_the_string_form(self, claude_config, claude_version, monkeypatch):
        # Negative control: what the flag changes.
        claude_version(None)
        monkeypatch.setattr(W, "_windows_short_path", lambda path: None)

        _inject()

        (hook,) = _hooks(claude_config)
        assert hook["command"] == QUOTED_COMMAND and "args" not in hook

    def test_with_the_flag_the_hook_is_command_plus_args(self, claude_config, claude_version, monkeypatch):
        claude_version(None)
        monkeypatch.setattr(W, "_windows_short_path", lambda path: None)

        with W.forced_exec_hooks():
            _inject()

        (hook,) = _hooks(claude_config)
        assert hook["command"] == LONG
        assert hook["args"] == ["-m", STOP]

    def test_the_flag_ends_with_its_block(self, claude_version):
        claude_version(None)
        with W.forced_exec_hooks():
            assert W._claude_code_exec_hooks(True) is True
        assert W._claude_code_exec_hooks(True) is False

    def test_other_platforms_keep_the_string_form(self, claude_version):
        claude_version(None)
        with W.forced_exec_hooks():
            assert W._claude_code_exec_hooks(False) is False


class TestRefusal:
    def test_a_known_old_claude_code_is_refused(self, claude_version):
        claude_version("2.1.100 (Claude Code)")
        message = W.hook_args_refusal(windows=True)
        assert "2.1.139" in message and "2.1.100" in message

    @pytest.mark.parametrize("output", [None, "2.1.139 (Claude Code)", "claude: unknown"])
    def test_an_unknown_or_new_enough_version_is_accepted(self, claude_version, output):
        claude_version(output)
        assert W.hook_args_refusal(windows=True) == ""

    def test_other_platforms_are_never_refused(self, claude_version):
        claude_version("2.1.100 (Claude Code)")
        assert W.hook_args_refusal(windows=False) == ""


class TestDoctorCli:
    @pytest.fixture
    def seen(self, monkeypatch):
        calls: list[dict] = []

        def run_doctor(fix=False, days=None):
            calls.append({"fix": fix, "forced": W._claude_code_exec_hooks(True)})
            return 0

        monkeypatch.setattr(W, "run_doctor", run_doctor)
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        return calls

    def test_fix_with_the_flag_forces_the_form(self, seen, claude_version):
        claude_version(None)
        assert W._run_doctor_cli(["--fix", "--hook-args"]) == 0
        assert seen == [{"fix": True, "forced": True}]
        assert W._claude_code_exec_hooks(True) is False  # nothing left behind

    def test_fix_without_the_flag_does_not(self, seen, claude_version):
        claude_version(None)
        assert W._run_doctor_cli(["--fix"]) == 0
        assert seen == [{"fix": True, "forced": False}]

    def test_the_flag_needs_fix(self, seen, claude_version, capsys):
        claude_version(None)
        assert W._run_doctor_cli(["--hook-args"]) == 2
        assert seen == []
        assert "--fix" in capsys.readouterr().err

    def test_a_known_old_claude_code_writes_nothing(self, seen, claude_version, capsys):
        claude_version("2.1.100 (Claude Code)")
        assert W._run_doctor_cli(["--fix", "--hook-args"]) == 2
        assert seen == []
        assert "2.1.139" in capsys.readouterr().err

    def test_other_platforms_ignore_the_flag_and_say_so(self, seen, claude_version, monkeypatch, capsys):
        claude_version(None)
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: False)
        assert W._run_doctor_cli(["--fix", "--hook-args"]) == 0
        assert seen == [{"fix": True, "forced": True}]  # asked as Windows by the probe only
        assert "only on Windows" in capsys.readouterr().err


class TestDoctorOutput:
    @pytest.fixture
    def run_doctor(self, tmp_path, monkeypatch, capsys, claude_config):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("ENGRAM_DIR", str(home / ".engram"))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        monkeypatch.setattr(W, "_find_python", lambda: LONG)
        monkeypatch.setattr(W, "_windows_short_path", lambda path: None)

        def run(*, fix: bool) -> str:
            capsys.readouterr()
            doctor._run_functional_checks(fix=fix)
            out = capsys.readouterr().out
            return out.split("-- Claude Code Hooks --")[1].split("PreCompact")[0]

        return run

    def test_a_stuck_hook_names_every_way_out(self, run_doctor, claude_config, claude_version):
        claude_version(None)
        _write(claude_config, {"type": "command", "command": QUOTED_COMMAND})

        section = run_doctor(fix=False)

        assert "[!!] Stop hook cannot start when Git Bash is not installed" in section
        assert "Git for Windows" in section
        assert "engram doctor --fix --hook-args" in section
        assert "2.1.139" in section
        assert "without a space" in section

    def test_fix_with_the_flag_converts_a_stuck_hook(self, run_doctor, claude_config, claude_version):
        claude_version(None)
        _write(claude_config, {"type": "command", "command": QUOTED_COMMAND, "async": True})

        # Negative control: plain --fix has nothing it can write.
        run_doctor(fix=True)
        assert _hooks(claude_config)[0]["command"] == QUOTED_COMMAND

        with W.forced_exec_hooks():
            section = run_doctor(fix=True)

        assert "[fixed] Stop hook rewritten in place" in section
        (hook,) = _hooks(claude_config)
        assert hook["command"] == LONG and hook["args"] == ["-m", STOP]
        assert hook["async"] is True

        section = run_doctor(fix=False)
        assert "[ok] Stop hook registered" in section
        assert "Git Bash" not in section


class TestSetupCli:
    def test_setup_takes_the_flag_out_before_the_wizard(self, monkeypatch, claude_version):
        claude_version(None)
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        seen: list[dict] = []
        monkeypatch.setattr(W, "run_setup", lambda **kwargs: seen.append(
            {"forced": W._claude_code_exec_hooks(True), **kwargs}))
        monkeypatch.setattr(W, "_start_usage_ping_cli", lambda: None)
        monkeypatch.setattr(W, "_skips_startup_effects", lambda args: True)
        monkeypatch.setattr("sys.argv", ["engram", "setup", "--hook-args"])

        W.main()

        assert seen == [{"forced": True, "advanced": False, "apply_external_config": False}]
        assert W._claude_code_exec_hooks(True) is False

    def test_scripted_setup_never_sees_the_flag(self, monkeypatch, claude_version):
        claude_version(None)
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        seen: list[dict] = []

        def run_agent_setup(argv):
            seen.append({"argv": list(argv), "forced": W._claude_code_exec_hooks(True)})
            return 0

        monkeypatch.setattr("piia_engram.agent_setup.run_agent_setup", run_agent_setup)
        monkeypatch.setattr("sys.argv", ["engram", "setup", "--non-interactive", "--hook-args"])

        with pytest.raises(SystemExit) as raised:
            W.main()

        assert raised.value.code == 0
        assert seen == [{"argv": ["--non-interactive"], "forced": True}]
