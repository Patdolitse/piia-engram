"""A hook command string for an interpreter path with a space, on Windows.

When Claude Code cannot be given ``command`` + ``args`` (no ``claude`` on
PATH, or one too old), the hook is one command string. With a space in the
interpreter path that string was quoted, which Git Bash runs and PowerShell
(Claude Code's fallback without Git Bash) does not. The Windows 8.3 short
path has no space, so it needs no quotes and every shell starts it. Where
the volume has no short names the quoted form stays, and doctor says that
this hook depends on Git Bash and what to do about it.

No real ``claude`` runs. Every ``settings.json`` is synthetic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor

STOP = "piia_engram.hooks.auto_save_on_stop"
LONG = r"C:\Program Files\Python312\python.exe"
SHORT = r"C:\PROGRA~1\Python312\python.exe"
SHORT_COMMAND = f"C:/PROGRA~1/Python312/python.exe -m {STOP}"
QUOTED_COMMAND = f'"C:/Program Files/Python312/python.exe" -m {STOP}'


@pytest.fixture
def short_names(monkeypatch):
    """Decide what the volume answers for a short path; returns the call log."""
    asked: list[str] = []

    def install(answer):
        def short(path: str):
            asked.append(path)
            return answer(path) if callable(answer) else answer

        monkeypatch.setattr(W, "_windows_short_path", short)
        return asked

    return install


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


def _read(config: Path) -> dict:
    return json.loads((config / "settings.json").read_text(encoding="utf-8"))


def _write(config: Path, *hooks: dict) -> Path:
    path = config / "settings.json"
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": list(hooks)}]}}, indent=2),
                    encoding="utf-8")
    return path


def _hooks(config: Path) -> list[dict]:
    return [hook for group in _read(config)["hooks"]["Stop"] for hook in group["hooks"]]


def _inject(python_path: str = LONG, **overrides):
    kwargs = {"event": "Stop", "module": STOP, "status_message": "s", "windows": True}
    kwargs.update(overrides)
    return W._inject_claude_code_hook_for_event(python_path, **kwargs)


class TestInterpreterForm:
    def test_path_with_a_space_becomes_its_short_path(self, short_names):
        asked = short_names(SHORT)
        assert W._quote_hook_interpreter(LONG, windows=True) == "C:/PROGRA~1/Python312/python.exe"
        assert len(asked) == 1

    def test_path_without_a_space_is_not_touched(self, short_names):
        asked = short_names(SHORT)
        plain = r"C:\Python312\python.exe"
        assert W._quote_hook_interpreter(plain, windows=True) == "C:/Python312/python.exe"
        assert asked == []

    @pytest.mark.parametrize("answer", [
        None,                                       # the call failed
        LONG,                                       # the volume has no short names
        r"C:\PROGRA~1\My Python\python.exe",        # only partly shortened
        r"C:\PROGRA~1\a&b\python.exe",              # still needs quoting
        "",
    ])
    def test_no_usable_short_path_keeps_the_quoted_form(self, short_names, answer):
        short_names(answer)
        assert (W._quote_hook_interpreter(LONG, windows=True)
                == '"C:/Program Files/Python312/python.exe"')

    def test_other_platforms_never_ask(self, short_names):
        asked = short_names(SHORT)
        assert W._quote_hook_interpreter("/opt/my python/bin/python3", windows=False) \
            == '"/opt/my python/bin/python3"'
        assert asked == []

    def test_a_missing_file_has_no_short_path(self, tmp_path):
        assert W._windows_short_path(str(tmp_path / "no such dir" / "python.exe")) is None

    def test_needs_git_bash_means_a_quoted_first_word(self):
        quoted = {"type": "command", "command": QUOTED_COMMAND}
        assert W._hook_needs_git_bash(quoted, windows=True)
        assert not W._hook_needs_git_bash(quoted, windows=False)
        assert not W._hook_needs_git_bash({"type": "command", "command": SHORT_COMMAND}, windows=True)
        assert not W._hook_needs_git_bash(
            {"type": "command", "command": LONG, "args": ["-m", STOP]}, windows=True)


def _shells() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    bash = shutil.which("bash")
    if bash:
        try:
            kernel = subprocess.run([bash, "-c", "uname -s"], capture_output=True, text=True,
                                    timeout=60).stdout
        except (OSError, subprocess.SubprocessError):
            kernel = ""
        if kernel.strip().startswith(("MSYS", "MINGW", "CYGWIN")):
            found["git-bash"] = [bash, "-c"]
    powershell = shutil.which("powershell.exe")
    if powershell:
        found["powershell"] = [powershell, "-NoProfile", "-NonInteractive", "-Command"]
    cmd = os.environ.get("ComSpec") or shutil.which("cmd.exe")
    if cmd:
        found["cmd"] = [cmd, "/d", "/s", "/c"]
    return found


@pytest.mark.skipif(os.name != "nt", reason="short paths exist on Windows only")
@pytest.mark.parametrize("shell", ["git-bash", "powershell", "cmd"])
def test_short_path_form_starts_in_every_shell(shell):
    """The written form, for a real program below a directory with a space,
    run by the shells Claude Code may hand a hook to."""
    program = next((p for p in (shutil.which("git"), shutil.which("powershell.exe"))
                    if p and " " in p), None)
    if program is None:
        pytest.skip("no program below a directory with a space")
    form = W._quote_hook_interpreter(program, windows=True)
    if form.startswith('"'):
        pytest.skip("this volume has no short names")
    assert " " not in form and "\\" not in form and "~" in form
    runner = _shells().get(shell)
    if runner is None:
        pytest.skip(f"{shell} not found")
    probe = "--version" if "git" in Path(program).name.lower() else "-NoProfile -Command exit"

    done = subprocess.run([*runner, f"{form} {probe}"], capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr


class TestRegistration:
    def test_new_string_hook_uses_the_short_path(self, claude_config, short_names):
        short_names(SHORT)
        assert _inject()
        (hook,) = _hooks(claude_config)
        assert hook["command"] == SHORT_COMMAND and "args" not in hook
        assert not W._hook_needs_git_bash(hook, windows=True)

    def test_command_plus_args_is_still_preferred(self, claude_config, short_names):
        asked = short_names(SHORT)
        assert _inject(exec_form=True)
        (hook,) = _hooks(claude_config)
        assert hook["command"] == LONG and hook["args"] == ["-m", STOP]

    def test_fix_rewrites_a_quoted_hook_with_the_short_path(self, claude_config, short_names):
        short_names(SHORT)
        mine = {"type": "command", "command": "echo mine"}
        _write(claude_config, mine, {"type": "command", "command": QUOTED_COMMAND, "async": False})

        assert _inject(force_rewrite=True)

        first, hook = _hooks(claude_config)
        assert first == mine
        assert hook["command"] == SHORT_COMMAND and hook["async"] is False
        assert _inject(force_rewrite=True) is None  # nothing left to do

    def test_fix_rewrites_the_kept_duplicate(self, claude_config, short_names):
        short_names(SHORT)
        _write(claude_config, {"type": "command", "command": QUOTED_COMMAND},
               {"type": "command", "command": QUOTED_COMMAND, "timeout": 4})

        assert _inject(force_rewrite=True)

        (hook,) = _hooks(claude_config)
        assert hook["command"] == SHORT_COMMAND

    def test_without_short_names_the_quoted_hook_stays(self, claude_config, short_names):
        short_names(None)
        path = _write(claude_config, {"type": "command", "command": QUOTED_COMMAND})
        mtime = path.stat().st_mtime_ns

        assert _inject(force_rewrite=True) is None
        assert path.stat().st_mtime_ns == mtime

    def test_setup_leaves_a_quoted_hook_alone(self, claude_config, short_names):
        """It runs under Git Bash; only doctor --fix changes a working hook."""
        short_names(SHORT)
        path = _write(claude_config, {"type": "command", "command": QUOTED_COMMAND})
        mtime = path.stat().st_mtime_ns

        assert _inject() is None
        assert path.stat().st_mtime_ns == mtime


class TestDoctor:
    @pytest.fixture
    def run_doctor(self, tmp_path, monkeypatch, capsys, claude_config):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        monkeypatch.setenv("ENGRAM_DIR", str(home / ".engram"))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        monkeypatch.setattr(W, "_find_python", lambda: LONG)

        def run(*, fix: bool) -> str:
            capsys.readouterr()
            doctor._run_functional_checks(fix=fix)
            return capsys.readouterr().out

        return run

    def test_quoted_hook_is_rewritten_with_the_short_path(self, run_doctor, claude_config, short_names):
        short_names(SHORT)
        _write(claude_config, {"type": "command", "command": QUOTED_COMMAND})

        out = run_doctor(fix=False)
        assert "[--] Stop hook starts only through Git Bash" in out
        assert "[!!] Stop hook" not in out
        assert _hooks(claude_config)[0]["command"] == QUOTED_COMMAND  # read-only

        out = run_doctor(fix=True)
        assert "[fixed] Stop hook rewritten in place" in out
        assert _hooks(claude_config)[0]["command"] == SHORT_COMMAND

        out = run_doctor(fix=False)
        assert "[ok] Stop hook registered" in out
        assert "Git Bash" not in out.split("-- Claude Code Hooks --")[1].split("PreCompact")[0]

    @pytest.mark.parametrize("fix", [False, True])
    def test_without_short_names_doctor_says_what_to_do(self, run_doctor, claude_config, short_names, fix):
        short_names(None)
        path = _write(claude_config, {"type": "command", "command": QUOTED_COMMAND})
        before = path.read_bytes()

        out = run_doctor(fix=fix)

        section = out.split("-- Claude Code Hooks --")[1].split("PreCompact")[0]
        assert "[!!] Stop hook cannot start when Git Bash is not installed" in section
        # The two ways out, then --fix.
        assert "claude" in section and "PATH" in section
        assert "without a space" in section
        assert "engram doctor --fix" in section
        assert "already up to date" not in section
        if not fix:
            assert path.read_bytes() == before
        else:
            assert _hooks(claude_config)[0]["command"] == QUOTED_COMMAND

    def test_unquoted_hook_is_healthy(self, run_doctor, claude_config, short_names):
        short_names(SHORT)
        _write(claude_config, {"type": "command", "command": SHORT_COMMAND})

        out = run_doctor(fix=False)

        section = out.split("-- Claude Code Hooks --")[1].split("PreCompact")[0]
        assert "[ok] Stop hook registered" in section
        assert "Git Bash" not in section
