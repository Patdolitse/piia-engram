"""Claude Code hook commands that survive Git Bash on Windows.

Engram registers its Claude Code hooks as one shell ``command`` string.
On Windows Claude Code hands that string to Git Bash, which reads each
backslash of a bare ``C:\\Users\\...\\python.exe`` as an escape, so the
hook exits 127. Doctor reported such a hook as healthy. Neither doctor
nor setup saw the same hook registered as ``command`` + ``args``, so
``--fix`` appended a second registration next to it.

Every ``settings.json`` here is synthetic: ``CLAUDE_CONFIG_DIR`` points
at a tmp dir, and the real ``~/.claude`` is never read or written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor

STOP = "piia_engram.hooks.auto_save_on_stop"
# The interpreter path from the user report. It has no spaces, so the old
# builder left it bare and Git Bash dropped every backslash.
WIN_PYTHON = r"C:\Users\u\AppData\Local\Programs\Python\Python314\python.exe"
WIN_PYTHON_SPACES = r"C:\Program Files\Python312\python.exe"
BARE_BACKSLASH_COMMAND = f"{WIN_PYTHON} -m {STOP}"  # what the old builder wrote
CURRENT_WINDOWS_COMMAND = WIN_PYTHON.replace("\\", "/") + f" -m {STOP}"
ENV_TAIL = (
    " --env ENGRAM_MIN_TURNS_TO_FLUSH=5"
    " --env CLAUDE_INVOKED_BY=engram_precompact"
)
# Hooks that are not Engram's; --fix must leave them exactly as written.
USER_STOP = {"type": "command", "command": "echo my-stop-hook"}
USER_PRECOMPACT = {"type": "command", "command": "echo my-precompact-hook", "timeout": 5}


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    """Claude Code's config dir, isolated: one synthetic settings.json per test."""
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


def _read(config: Path) -> dict:
    return json.loads((config / "settings.json").read_text(encoding="utf-8"))


def _write(config: Path, settings: dict) -> None:
    (config / "settings.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _event_hooks(settings: dict, event: str) -> list[dict]:
    """Every hook under ``event``, Engram's or not, in file order."""
    return [
        hook
        for group in settings.get("hooks", {}).get(event, [])
        for hook in group.get("hooks", [])
    ]


def _engram_hooks(settings: dict, event: str) -> list[dict]:
    return [
        hook for hook in _event_hooks(settings, event)
        if STOP in W._hook_marker_text(hook)
    ]


def _inject(python_path: str = WIN_PYTHON, **overrides) -> str | None:
    """Register the Stop hook with this test's defaults (Windows form)."""
    kwargs = {
        "event": "Stop", "module": STOP, "status_message": "Engram 会话自动保存...",
        "windows": True,
    }
    kwargs.update(overrides)
    return W._inject_claude_code_hook_for_event(python_path, **kwargs)


# Three ways the same Engram hook can be registered.
def _exec_form(**extra) -> dict:
    """``command`` + ``args``: Claude Code starts it without a shell."""
    return {"type": "command", "command": WIN_PYTHON, "args": ["-m", STOP], **extra}


def _bare_backslash(**extra) -> dict:
    """What older Engram versions wrote on Windows; Git Bash cannot run it."""
    return {"type": "command", "command": BARE_BACKSLASH_COMMAND, **extra}


def _current(**extra) -> dict:
    """What the builder writes on Windows now."""
    return {"type": "command", "command": CURRENT_WINDOWS_COMMAND, **extra}


def _custom(**extra) -> dict:
    """Mentions the marker in a command of its own: Engram's by the matcher."""
    return {"type": "command", "command": f"my-wrapper.sh --module {STOP}", **extra}


def _with_async(hook: dict, value) -> dict:
    """``async`` is a keyword, so it cannot be passed as a keyword argument."""
    hook["async"] = value
    return hook


# ---------------------------------------------------------------------------
# the command builder
# ---------------------------------------------------------------------------


class TestHookInterpreterQuoting:
    """``_build_engram_hook_command``: the Windows form, and no change elsewhere."""

    def test_windows_path_gets_forward_slashes_and_stays_bare(self):
        """No spaces, so nothing needs quoting; bare forward slashes run in
        Git Bash, cmd.exe and PowerShell alike."""
        cmd = W._build_engram_hook_command(WIN_PYTHON, module=STOP, windows=True)
        assert cmd == CURRENT_WINDOWS_COMMAND
        assert "\\" not in cmd and not cmd.startswith('"')

    def test_windows_path_with_spaces_is_quoted(self):
        cmd = W._build_engram_hook_command(
            WIN_PYTHON_SPACES, module=STOP,
            extra_env={"CLAUDE_INVOKED_BY": "engram_precompact"}, windows=True,
        )
        assert cmd == (
            '"C:/Program Files/Python312/python.exe" -m ' + STOP
            + " --env CLAUDE_INVOKED_BY=engram_precompact"
        )

    def test_windows_network_path(self):
        cmd = W._build_engram_hook_command(
            r"\\server\share\python.exe", module=STOP, windows=True,
        )
        assert cmd == f"//server/share/python.exe -m {STOP}"

    @pytest.mark.parametrize(
        ("python_path", "head"),
        [
            ("/usr/bin/python3", "/usr/bin/python3"),
            (r"C:\Python312\python.exe", r"C:\Python312\python.exe"),
            (r"C:\Program Files\Python312\python.exe", r'"C:\Program Files\Python312\python.exe"'),
            ("/opt/my py/python3", '"/opt/my py/python3"'),
        ],
    )
    def test_other_platforms_output_is_unchanged(self, python_path, head):
        """Byte for byte what the builder wrote before the fix."""
        cmd = W._build_engram_hook_command(
            python_path,
            module=STOP,
            extra_env={
                "ENGRAM_MIN_TURNS_TO_FLUSH": "5",
                "CLAUDE_INVOKED_BY": "engram_precompact",
            },
            windows=False,
        )
        assert cmd == f"{head} -m {STOP}{ENV_TAIL}"

    @pytest.mark.skipif(os.name == "nt", reason="this is the Windows form's platform")
    def test_default_output_is_the_unchanged_one_off_windows(self):
        for python_path in ("/usr/bin/python3", r"C:\Python312\python.exe", "/opt/my py/python3"):
            native = W._build_engram_hook_command(python_path, module=STOP)
            assert native == W._build_engram_hook_command(python_path, module=STOP, windows=False)

    def test_platform_decision_follows_os_name(self):
        assert W._hook_shell_is_git_bash() is (os.name == "nt")


# ---------------------------------------------------------------------------
# which registered hooks Git Bash cannot run
# ---------------------------------------------------------------------------


class TestShellUnsafeCommandDetection:
    """``_hook_command_has_unquoted_backslash``: what doctor flags instead of ``[ok]``."""

    def test_bare_windows_path_is_flagged_on_windows_only(self):
        hook = _bare_backslash()
        assert W._hook_command_has_unquoted_backslash(hook, windows=True) is True
        assert W._hook_command_has_unquoted_backslash(hook, windows=False) is False

    @pytest.mark.parametrize(
        "command",
        [
            # A backslash inside double quotes survives Git Bash.
            f'"C:\\Program Files\\Python312\\python.exe" -m {STOP}',
            f"C:/Users/u/python.exe -m {STOP}",
            CURRENT_WINDOWS_COMMAND,
        ],
    )
    def test_runnable_commands_are_not_flagged(self, command):
        assert W._hook_command_has_unquoted_backslash({"command": command}, windows=True) is False

    def test_exec_form_is_never_flagged(self):
        """Its ``command`` is the bare interpreter path, but no shell reads it."""
        assert W._hook_command_has_unquoted_backslash(_exec_form(), windows=True) is False

    def test_only_the_interpreter_is_judged(self):
        """An argument after ``-m`` is not the interpreter path. Without
        ``-m`` (script-path style) the whole command is read."""
        after_module = {"command": f"{CURRENT_WINDOWS_COMMAND} --env ROOT=C:\\data"}
        assert W._hook_command_has_unquoted_backslash(after_module, windows=True) is False
        script_style = {"command": f'{WIN_PYTHON} "C:\\engram\\auto_save_on_stop.py"'}
        assert W._hook_command_has_unquoted_backslash(script_style, windows=True) is True

    def test_default_platform_is_the_injectable_decision(self, monkeypatch):
        hook = _bare_backslash()
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: False)
        assert W._hook_command_has_unquoted_backslash(hook) is False
        monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
        assert W._hook_command_has_unquoted_backslash(hook) is True


# ---------------------------------------------------------------------------
# marker matching, shared by doctor and the injector
# ---------------------------------------------------------------------------


PRECOMPACT_MARKERS = ("CLAUDE_INVOKED_BY=engram_precompact", STOP)


class TestMarkerMatching:
    def test_exec_form_is_an_engram_hook(self):
        hook = _exec_form()
        settings = {"hooks": {"Stop": [{"hooks": [dict(USER_STOP), hook]}]}}
        matches = W._matching_event_hooks(settings, "Stop", ("auto_save_on_stop",))
        assert [id(found) for _, found in matches] == [id(hook)]

    def test_markers_may_be_split_between_command_and_args(self):
        """PreCompact wants both markers; an env prefix in ``command`` and the
        module in ``args`` is still that hook."""
        hook = {"command": "CLAUDE_INVOKED_BY=engram_precompact python", "args": ["-m", STOP]}
        settings = {"hooks": {"PreCompact": [{"hooks": [hook]}]}}
        matches = W._matching_event_hooks(
            settings, "PreCompact", PRECOMPACT_MARKERS, all_required=True,
        )
        assert [found for _, found in matches] == [hook]

    def test_all_required_still_needs_every_marker(self):
        """A Stop-style hook under PreCompact lacks the env marker (M3)."""
        hook = {"command": f"/usr/bin/python3 -m {STOP}"}
        settings = {"hooks": {"PreCompact": [{"hooks": [hook]}]}}
        assert W._matching_event_hooks(
            settings, "PreCompact", PRECOMPACT_MARKERS, all_required=True,
        ) == []

    def test_other_hooks_and_events_are_not_engrams(self):
        settings = {
            "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "echo piia", "args": ["-m", "json.tool"]}]}],
                "PreCompact": [{"hooks": [_exec_form()]}],
            },
        }
        assert W._matching_event_hooks(settings, "Stop", ("piia_engram",)) == []
        # Registered under PreCompact only; Stop does not borrow it.
        assert W._matching_event_hooks(settings, "Stop", ("auto_save_on_stop",)) == []

    @pytest.mark.parametrize(
        "settings",
        [
            [],
            {"hooks": []},
            {"hooks": {"Stop": "piia_engram.hooks.auto_save_on_stop"}},
            {"hooks": {"Stop": ["piia_engram", {"hooks": "x"}, {"hooks": [1, None, "piia_engram"]}]}},
        ],
    )
    def test_malformed_sections_yield_nothing(self, settings):
        assert W._matching_event_hooks(settings, "Stop", ("piia_engram",)) == []

    def test_integrity_report_counts_exec_form_as_registered(self, claude_config):
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [_exec_form()]}]}})
        (row,) = [r for r in doctor._claude_hook_rows(Path.home()) if r["event"] == "Stop"]
        assert row["registered"] is True
        assert "command" not in row  # the report stays metadata-only


# ---------------------------------------------------------------------------
# the injector
# ---------------------------------------------------------------------------


class TestInjectorRepairs:
    def test_windows_registration_uses_forward_slashes(self, claude_config):
        result = _inject()
        assert result == str(claude_config / "settings.json")
        (hook,) = _engram_hooks(_read(claude_config), "Stop")
        assert hook["command"] == CURRENT_WINDOWS_COMMAND
        assert hook["async"] is True

    def test_windows_registration_with_spaces_is_quoted(self, claude_config):
        _inject(WIN_PYTHON_SPACES)
        (hook,) = _engram_hooks(_read(claude_config), "Stop")
        assert hook["command"] == f'"C:/Program Files/Python312/python.exe" -m {STOP}'

    def test_other_platform_registration_is_unchanged(self, claude_config):
        _inject("/usr/bin/python3", windows=False)
        (hook,) = _engram_hooks(_read(claude_config), "Stop")
        assert hook["command"] == f"/usr/bin/python3 -m {STOP}"

    def test_exec_form_counts_as_registered(self, claude_config):
        """Setup's idempotent skip sees it, so no second hook is added."""
        written = {"hooks": {"Stop": [{"hooks": [_exec_form()]}]}}
        _write(claude_config, written)
        assert _inject() is None
        assert _read(claude_config) == written

    def test_exec_form_is_never_rewritten_or_converted(self, claude_config):
        _write(claude_config, {"hooks": {"Stop": [
            {"hooks": [_with_async(_exec_form(), True)]},
        ]}})
        path = claude_config / "settings.json"
        mtime = path.stat().st_mtime_ns

        assert _inject(force_rewrite=True) is None

        assert path.stat().st_mtime_ns == mtime  # not even touched

    def test_force_repairs_bare_backslash_command(self, claude_config):
        """Rewritten where it stands; ``async`` and the neighbours as found."""
        bare = _with_async(_bare_backslash(timeout=10, statusMessage="old"), True)
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [dict(USER_STOP), bare]}]}})

        assert _inject(force_rewrite=True) is not None

        settings = _read(claude_config)
        hooks = _event_hooks(settings, "Stop")
        assert hooks[0] == USER_STOP
        (engram,) = _engram_hooks(settings, "Stop")
        assert hooks[1] is engram
        assert engram["command"] == CURRENT_WINDOWS_COMMAND
        assert engram["timeout"] == 30
        assert engram["async"] is True

        # Already current: a second --fix neither rewrites nor touches the file.
        path = claude_config / "settings.json"
        mtime = path.stat().st_mtime_ns
        assert _inject(force_rewrite=True) is None
        assert path.stat().st_mtime_ns == mtime

    def test_stale_hook_rewrite_keeps_async_as_found(self, claude_config):
        """The older repair path (stale command) keeps ``async`` as found
        too, so a deliberate synchronous hook survives."""
        _write(claude_config, {"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "python auto_save_on_stop.py", "async": False}]},
        ]}})

        # The Stop wrapper's own marker: the script path, not the module name.
        assert _inject(force_rewrite=True, marker_keywords=("auto_save_on_stop",)) is not None

        (engram,) = _event_hooks(_read(claude_config), "Stop")  # in place, not appended
        assert engram["command"] == CURRENT_WINDOWS_COMMAND
        assert engram["async"] is False

    # -- registered more than once: keep one, remove the rest ---------------

    @pytest.mark.parametrize("exec_first", [False, True])
    def test_duplicates_keep_the_exec_form_hook(self, claude_config, exec_first):
        """The state older ``--fix`` runs left: a command+args hook and a
        second registration. The command+args hook stays as written,
        wherever it stands; the other goes, with an emptied group."""
        exec_hook = _with_async(_exec_form(timeout=7), False)
        bare = _with_async(_bare_backslash(timeout=30, statusMessage="old"), True)
        first = [dict(USER_STOP), exec_hook if exec_first else bare]
        second = [bare if exec_first else exec_hook]
        _write(claude_config, {"hooks": {"Stop": [{"hooks": first}, {"hooks": second}]}})

        assert _inject(force_rewrite=True) is not None

        # It stays where it stood; the other group goes once it is empty.
        groups = (
            [{"hooks": [USER_STOP, exec_hook]}]
            if exec_first
            else [{"hooks": [USER_STOP]}, {"hooks": [exec_hook]}]
        )
        assert _read(claude_config) == {"hooks": {"Stop": groups}}

    def test_duplicates_keep_the_first_exec_form_hook(self, claude_config):
        first = _with_async(_exec_form(), False)
        second = _with_async(_exec_form(), True)
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [first, second]}]}})

        assert _inject(force_rewrite=True) is not None

        assert _read(claude_config) == {"hooks": {"Stop": [{"hooks": [first]}]}}

    @pytest.mark.parametrize("current_first", [False, True])
    def test_duplicates_keep_the_current_hook(self, claude_config, current_first):
        current = _with_async(_current(), True)
        bare = _bare_backslash()
        hooks = [current, bare] if current_first else [bare, current]
        _write(claude_config, {"hooks": {"Stop": [{"hooks": hooks}]}})

        assert _inject(force_rewrite=True) is not None

        assert _read(claude_config) == {"hooks": {"Stop": [{"hooks": [current]}]}}

    def test_duplicates_fall_back_to_the_first_and_repair_it(self, claude_config):
        """No command+args hook and no current one: the first stays, is
        rewritten where it stands, and keeps the ``async`` it had."""
        first = _with_async(_bare_backslash(), False)
        _write(claude_config, {"hooks": {"Stop": [
            {"hooks": [first]},
            {"hooks": [_with_async(_bare_backslash(), True)]},
        ]}})

        assert _inject(force_rewrite=True) is not None

        (engram,) = _engram_hooks(_read(claude_config), "Stop")
        assert engram["command"] == CURRENT_WINDOWS_COMMAND
        assert engram["async"] is False

    def test_duplicates_leave_a_runnable_first_hook_alone(self, claude_config):
        """The fallback is rewritten only when Git Bash cannot run it."""
        custom = _with_async(_custom(), False)
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [custom, _bare_backslash()]}]}})

        assert _inject(force_rewrite=True) is not None

        assert _read(claude_config) == {"hooks": {"Stop": [{"hooks": [custom]}]}}


# ---------------------------------------------------------------------------
# Git Bash regression: run what the builder writes
# ---------------------------------------------------------------------------


def _git_bash() -> str | None:
    """The bash Claude Code runs hooks in, when there is one to test against.

    On Windows that is an MSYS bash (Git Bash). ``bash`` on PATH can also be
    the WSL launcher, which is a different shell on another system.
    """
    bash = shutil.which("bash")
    if not bash:
        return None
    if os.name != "nt":
        return bash
    try:
        kernel = subprocess.run(
            [bash, "-c", "uname -s"], capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if kernel.returncode != 0 or not kernel.stdout.strip().startswith(("MSYS", "MINGW", "CYGWIN")):
        return None
    return bash


@pytest.fixture
def git_bash() -> str:
    bash = _git_bash()
    if bash is None:
        pytest.skip("no MSYS bash (Git Bash) on PATH")
    return bash


def _stub_interpreter(path: Path) -> Path:
    """A stand-in python that prints its path, then each argument, one per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" "$@"\n', encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _under_bash(bash: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [bash, "-c", command], capture_output=True, text=True, timeout=120,
    )


class TestGitBashRegression:
    """The generated command, run through the shells that execute hooks."""

    @pytest.mark.parametrize("relative", ["python3", "py stub/python3"])
    def test_generated_command_survives_git_bash(self, git_bash, tmp_path, relative):
        """Without a space in the path the Windows form stays bare; with
        one it is quoted. Both arrive as one argument."""
        stub = _stub_interpreter(tmp_path / relative)
        command = W._build_engram_hook_command(
            str(stub), module=STOP, extra_env={"CLAUDE_INVOKED_BY": "engram_postcompact"},
        )
        if os.name == "nt":
            expected = str(stub).replace("\\", "/")
            if " " in expected:
                expected = f'"{expected}"'
            assert command == f"{expected} -m {STOP} --env CLAUDE_INVOKED_BY=engram_postcompact"

        done = _under_bash(git_bash, command)

        assert done.returncode == 0, done.stderr
        lines = done.stdout.splitlines()
        assert lines[1:] == ["-m", STOP, "--env", "CLAUDE_INVOKED_BY=engram_postcompact"]
        # The path arrived as one argument, exactly as written.
        assert lines[0].replace("\\", "/").lower() == str(stub).replace("\\", "/").lower()

    @pytest.mark.skipif(os.name != "nt", reason="bare backslash paths exist on Windows only")
    def test_bare_backslash_path_is_lost_under_git_bash(self, git_bash, tmp_path):
        """What the old builder wrote: Git Bash drops every backslash and
        finds no command."""
        stub = _stub_interpreter(tmp_path / "python3")
        legacy = f"{stub} -m {STOP}"
        assert "\\" in legacy and not legacy.startswith('"')

        done = _under_bash(git_bash, legacy)

        assert done.returncode == 127
        assert "command not found" in done.stderr

    @pytest.mark.skipif(os.name != "nt", reason="backslash paths exist on Windows only")
    def test_quoted_backslash_path_survives_git_bash(self, git_bash, tmp_path):
        """Why doctor flags a backslash only outside double quotes: inside
        them Git Bash keeps it."""
        stub = _stub_interpreter(tmp_path / "py stub" / "python3")
        done = _under_bash(git_bash, f'"{stub}" -m {STOP}')
        assert done.returncode == 0, done.stderr
        assert done.stdout.splitlines()[1:] == ["-m", STOP]

    @pytest.mark.skipif(os.name != "nt", reason="cmd.exe and PowerShell exist on Windows only")
    @pytest.mark.parametrize("shell", ["cmd", "powershell"])
    def test_windows_form_runs_without_git_bash(self, shell):
        """Claude Code falls back to PowerShell when Git Bash is missing, and
        older runners used cmd.exe. Both start a bare ``C:/.../python.exe``."""
        if shell == "cmd":
            exe = os.environ.get("ComSpec") or shutil.which("cmd.exe")
        else:
            exe = shutil.which("powershell.exe")
            if " " in sys.executable:
                pytest.skip("a quoted path is for Git Bash and cmd.exe only")
        if not exe:
            pytest.skip(f"{shell} not found")
        prefix = W._build_engram_hook_command(
            sys.executable, module=STOP, windows=True,
        ).split(" -m ", 1)[0]
        assert "\\" not in prefix

        probe = f'{prefix} -c "import sys; print(1)"'
        if shell == "cmd":
            done = subprocess.run(
                f'{exe} /d /s /c "{probe}"', capture_output=True, text=True, timeout=120,
            )
        else:
            done = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", probe],
                capture_output=True, text=True, timeout=120,
            )

        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == "1"


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@pytest.fixture
def run_doctor(tmp_path, monkeypatch, capsys, claude_config):
    """Run doctor's functional checks on an isolated store and home.

    The machine under test is the one from the report: hooks are judged
    and rebuilt as on Windows, whatever platform runs the test.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    # The default location: a hook process finds it without being told.
    monkeypatch.setenv("ENGRAM_DIR", str(home / ".engram"))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setattr(W, "_hook_shell_is_git_bash", lambda: True)
    monkeypatch.setattr(W, "_find_python", lambda: WIN_PYTHON)

    def run(*, fix: bool) -> str:
        capsys.readouterr()
        doctor._run_functional_checks(fix=fix)
        return capsys.readouterr().out

    return run


def _other_events() -> dict:
    """Hooks that are not Engram's, under the other events."""
    return {
        "PreCompact": [{"hooks": [dict(USER_PRECOMPACT)]}],
        "SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": "echo my-start-hook"},
        ]}],
    }


class TestDoctorHookHealth:
    """Doctor flags a hook Git Bash cannot run and a hook registered twice, and ``--fix`` repairs both in place."""

    def test_bare_backslash_command_is_flagged_then_repaired(self, run_doctor, claude_config):
        legacy = _with_async(
            _bare_backslash(timeout=30, statusMessage="Engram 会话自动保存..."), True,
        )
        written = {"hooks": {"Stop": [{"hooks": [dict(USER_STOP), legacy]}]}}
        _write(claude_config, written)

        out = run_doctor(fix=False)

        assert "[!!] Stop hook uses a legacy or shell-unsafe command" in out
        assert "[ok] Stop hook registered" not in out
        assert "Run 'engram doctor --fix' to rewrite the Stop hook in place." in out
        assert _read(claude_config) == written  # looking changes nothing

        out = run_doctor(fix=True)

        assert "[fixed] Stop hook rewritten in place:" in out
        settings = _read(claude_config)
        hooks = _event_hooks(settings, "Stop")
        assert hooks[0] == USER_STOP
        (engram,) = _engram_hooks(settings, "Stop")
        assert hooks[1] is engram
        assert engram["command"] == CURRENT_WINDOWS_COMMAND
        assert engram["async"] is True  # as found

        out = run_doctor(fix=False)

        assert "[ok] Stop hook registered" in out
        assert "Stop hook uses a legacy" not in out

    def test_exec_form_is_healthy_and_left_alone(self, run_doctor, claude_config):
        exec_hook = _with_async(_exec_form(timeout=7), False)
        written = {"hooks": {"Stop": [{"hooks": [dict(USER_STOP), exec_hook]}], **_other_events()}}

        _write(claude_config, written)
        out = run_doctor(fix=False)

        assert "[ok] Stop hook registered" in out
        assert "Stop hook uses a legacy" not in out
        assert "No Engram Stop hook" not in out

        out = run_doctor(fix=True)

        assert "Stop hook rewritten" not in out
        assert "Stop hook registered:" not in out
        # Not converted to a command string; everything of the user's as written.
        settings = _read(claude_config)
        assert settings["hooks"]["Stop"] == written["hooks"]["Stop"]
        assert USER_PRECOMPACT in _event_hooks(settings, "PreCompact")
        assert {"type": "command", "command": "echo my-start-hook"} in _event_hooks(settings, "SessionStart")
        assert settings["hooks"]["SessionStart"][0]["matcher"] == "startup"

    def test_current_command_is_healthy(self, run_doctor, claude_config):
        _write(claude_config, {"hooks": {"Stop": [{"hooks": [_current()]}]}})

        out = run_doctor(fix=False)

        assert "[ok] Stop hook registered" in out
        assert "legacy or shell-unsafe" not in out
        assert "hook registered 2 times" not in out

    def test_duplicate_with_unsafe_command_is_reported_then_reduced(self, run_doctor, claude_config):
        exec_hook = _with_async(_exec_form(timeout=7), False)
        bare = _with_async(_bare_backslash(timeout=30, statusMessage="old"), True)
        _write(claude_config, {"hooks": {"Stop": [
            {"hooks": [dict(USER_STOP), exec_hook]},
            {"hooks": [bare]},
        ], **_other_events()}})

        out = run_doctor(fix=False)

        assert "[!!] Stop hook uses a legacy or shell-unsafe command" in out
        assert "[!!] Stop hook registered 2 times" in out
        assert "Run 'engram doctor --fix' to keep one Stop hook." in out

        out = run_doctor(fix=True)

        assert "[fixed] Stop hook: one registration kept, 1 duplicate registration(s) removed:" in out
        settings = _read(claude_config)
        # The command+args hook is the one kept, exactly as written.
        assert settings["hooks"]["Stop"] == [{"hooks": [USER_STOP, exec_hook]}]
        assert [h for h in _event_hooks(settings, "PreCompact") if "piia_engram" not in h["command"]] == [USER_PRECOMPACT]
        assert {"type": "command", "command": "echo my-start-hook"} in _event_hooks(settings, "SessionStart")

        out = run_doctor(fix=False)

        assert "[ok] Stop hook registered" in out
        assert "Stop hook uses a legacy" not in out
        assert "Stop hook registered 2 times" not in out

    def test_duplicates_without_unsafe_command(self, run_doctor, claude_config):
        """Registered twice, both runnable: still reported, and ``--fix``
        keeps one. The kept hook keeps the ``async`` it had."""
        exec_hook = _with_async(_exec_form(), False)
        _write(claude_config, {"hooks": {"Stop": [
            {"hooks": [_with_async(_current(), True), exec_hook]},
        ]}})

        out = run_doctor(fix=False)

        assert "[ok] Stop hook registered" in out
        assert "[!!] Stop hook registered 2 times" in out
        assert "shell-unsafe" not in out

        out = run_doctor(fix=True)

        assert "[fixed] Stop hook: one registration kept, 1 duplicate registration(s) removed:" in out
        # (--fix registers the other events in the same run; Stop is what matters here.)
        assert _read(claude_config)["hooks"]["Stop"] == [{"hooks": [exec_hook]}]

    def test_without_python_the_hook_is_left_as_is(self, run_doctor, claude_config, monkeypatch):
        written = {"hooks": {"Stop": [{"hooks": [_bare_backslash()]}]}}
        _write(claude_config, written)
        monkeypatch.setattr(W, "_find_python", lambda: None)

        out = run_doctor(fix=True)

        assert "[info] Cannot fix: Python not found" in out
        assert _read(claude_config)["hooks"]["Stop"] == written["hooks"]["Stop"]
