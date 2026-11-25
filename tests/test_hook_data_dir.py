"""Claude Code hooks reach the data directory setup was given.

A hook is started by Claude Code, not by the Engram MCP server, so it does
not see the ``ENGRAM_DIR`` that setup writes into the MCP entry. With a data
directory outside the default location and no machine-wide ``ENGRAM_DIR``,
the hook read (and queued into) ``~/.engram`` instead. The hook command now
carries the directory as ``--env ENGRAM_DIR=...``, which every hook module
already applies before it opens the store.

Every ``settings.json`` here is synthetic: ``CLAUDE_CONFIG_DIR`` points at a
tmp dir, and the real ``~/.claude`` is never read or written.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor, storage
from piia_engram.hooks import auto_absorb_compact, auto_inject_resume_brief, auto_save_on_stop

PY = "/usr/bin/python3"
WIN_PY = r"C:\Users\u\AppData\Local\Programs\Python\Python314\python.exe"

INJECTORS = {
    "Stop": (W._inject_claude_code_hook, auto_save_on_stop),
    "PreCompact": (W._inject_claude_code_precompact_hook, auto_save_on_stop),
    "SessionStart": (W._inject_claude_code_sessionstart_hook, auto_inject_resume_brief),
    "PostCompact": (W._inject_claude_code_postcompact_hook, auto_absorb_compact),
}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def claude_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "claude-config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


def _read(config: Path) -> dict:
    return json.loads((config / "settings.json").read_text(encoding="utf-8"))


def _write(config: Path, settings: dict) -> None:
    (config / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _hooks(settings: dict, event: str) -> list[dict]:
    return [hook for group in settings.get("hooks", {}).get(event, [])
            for hook in group.get("hooks", [])]


def _hook_argv(hook: dict) -> list[str]:
    """The arguments the hook's Python process is started with."""
    if isinstance(hook.get("args"), list):
        return list(hook["args"])
    return shlex.split(hook["command"])[1:]


def _root_in_hook_process(hook: dict, module, monkeypatch) -> Path:
    """The store root a hook process resolves when Claude Code itself was
    started without ``ENGRAM_DIR``.

    ``_apply_argv_env`` writes every ``--env KEY=VAL`` into ``os.environ``. In
    a hook process that ends with the process; here it would stay for every
    later test (``CLAUDE_INVOKED_BY`` makes the hooks return early). Each key
    is therefore handed to ``monkeypatch`` first, which puts it back.
    """
    argv = _hook_argv(hook)
    keys = {"ENGRAM_DIR"}
    keys.update(argv[index + 1].partition("=")[0].strip()
                for index, arg in enumerate(argv[:-1]) if arg == "--env")
    for key in sorted(filter(None, keys)):
        # delenv alone records nothing for a variable that is not set.
        monkeypatch.setenv(key, "recorded-for-restore")
        monkeypatch.delenv(key)
    module._apply_argv_env(argv)
    return storage._engram_root()


@pytest.mark.parametrize("event", sorted(INJECTORS))
def test_reading_a_hook_command_leaves_the_environment_as_it_was(
        event, home, claude_config, tmp_path):
    custom = tmp_path / "store"
    custom.mkdir()
    injector, module = INJECTORS[event]
    assert injector(PY, file_safety_root=custom, authorized_external_write=True)
    (hook,) = _hooks(_read(claude_config), event)
    before = dict(os.environ)

    with pytest.MonkeyPatch.context() as patch:
        assert _root_in_hook_process(hook, module, patch) == custom.resolve()
        assert os.environ["ENGRAM_DIR"] == custom.resolve().as_posix()
        if event != "Stop":
            assert os.environ["CLAUDE_INVOKED_BY"].startswith("engram_")

    assert dict(os.environ) == before
    assert "CLAUDE_INVOKED_BY" not in os.environ


@pytest.mark.parametrize("event", sorted(INJECTORS))
def test_setup_hook_reaches_a_custom_data_dir(event, home, claude_config, tmp_path, monkeypatch):
    """Registered the way setup registers it: the chosen directory is passed as
    ``file_safety_root`` and nothing sets ``ENGRAM_DIR`` for Claude Code."""
    custom = tmp_path / "My Data" / "engram"
    custom.mkdir(parents=True)
    injector, module = INJECTORS[event]

    assert injector(PY, file_safety_root=custom, authorized_external_write=True)

    (hook,) = _hooks(_read(claude_config), event)
    assert _root_in_hook_process(hook, module, monkeypatch) == custom.resolve()


def test_default_data_dir_adds_nothing(home, claude_config, monkeypatch):
    default = home / ".engram"
    default.mkdir()

    assert W._inject_claude_code_sessionstart_hook(
        PY, file_safety_root=default, authorized_external_write=True)

    (hook,) = _hooks(_read(claude_config), "SessionStart")
    assert "ENGRAM_DIR" not in hook["command"]
    assert _root_in_hook_process(hook, auto_inject_resume_brief, monkeypatch) == default.resolve()


def test_legacy_default_data_dir_adds_nothing(home, claude_config):
    legacy = home / ".piia"
    legacy.mkdir()
    assert W._hook_data_dir(legacy) is None
    assert W._hook_data_dir(None) is None


def test_windows_value_has_no_backslash(home, claude_config, tmp_path):
    """Git Bash drops a bare backslash, in an argument as in the interpreter path."""
    custom = tmp_path / "store"
    custom.mkdir()
    W._inject_claude_code_hook_for_event(
        WIN_PY, event="Stop", module="piia_engram.hooks.auto_save_on_stop",
        status_message="s", windows=True, data_dir=custom)
    (hook,) = _hooks(_read(claude_config), "Stop")
    assert "\\" not in hook["command"]
    assert f"ENGRAM_DIR={custom.resolve().as_posix()}" in hook["command"]


class TestExistingHooks:
    SS = "piia_engram.hooks.auto_inject_resume_brief"
    TAIL = " --env CLAUDE_INVOKED_BY=engram_session_start"

    def _custom(self, tmp_path) -> Path:
        custom = tmp_path / "store"
        custom.mkdir()
        return custom

    def test_lacking_hook_is_detected(self, home, tmp_path):
        custom = self._custom(tmp_path)
        wanted = W._hook_data_dir(custom)
        bare = {"type": "command", "command": f"{PY} -m {self.SS}{self.TAIL}"}
        pinned = {"type": "command",
                  "command": f"{PY} -m {self.SS}{self.TAIL} --env ENGRAM_DIR={wanted}"}
        exec_pinned = {"type": "command", "command": PY,
                       "args": ["-m", self.SS, "--env", f"ENGRAM_DIR={wanted}"]}
        wrapper = {"type": "command", "command": "my-wrapper.sh auto_inject_resume_brief"}

        assert W._hook_lacks_data_dir(bare, wanted)
        assert not W._hook_lacks_data_dir(pinned, wanted)
        assert not W._hook_lacks_data_dir(exec_pinned, wanted)
        assert not W._hook_lacks_data_dir(bare, None)
        # Not a ``python -m piia_engram.hooks.*`` call: the owner's own script.
        assert not W._hook_lacks_data_dir(wrapper, wanted)

    def test_setup_adds_it_to_an_existing_hook_in_place(self, home, claude_config, tmp_path, monkeypatch):
        custom = self._custom(tmp_path)
        other = {"type": "command", "command": "echo mine"}
        old = {"type": "command", "command": f"{PY} -m {self.SS}{self.TAIL}",
               "timeout": 15, "statusMessage": "x"}
        _write(claude_config, {"hooks": {"SessionStart": [{"hooks": [other, old]}]}})

        assert W._inject_claude_code_sessionstart_hook(
            PY, file_safety_root=custom, authorized_external_write=True)

        hooks = _hooks(_read(claude_config), "SessionStart")
        assert len(hooks) == 2 and hooks[0] == other
        assert _root_in_hook_process(hooks[1], auto_inject_resume_brief, monkeypatch) == custom.resolve()
        # A second run has nothing left to do.
        assert W._inject_claude_code_sessionstart_hook(
            PY, file_safety_root=custom, authorized_external_write=True) is None

    def test_exec_form_gets_the_argument_and_nothing_else_changes(self, home, claude_config, tmp_path, monkeypatch):
        custom = self._custom(tmp_path)
        old = {"type": "command", "command": WIN_PY, "timeout": 7,
               "args": ["-m", self.SS, "--env", "CLAUDE_INVOKED_BY=engram_session_start",
                        "--env", "ENGRAM_DIR=/somewhere/else"]}
        _write(claude_config, {"hooks": {"SessionStart": [{"hooks": [dict(old)]}]}})

        assert W._inject_claude_code_sessionstart_hook(
            PY, force_rewrite=True, file_safety_root=custom, authorized_external_write=True)

        (hook,) = _hooks(_read(claude_config), "SessionStart")
        assert hook["command"] == WIN_PY and hook["timeout"] == 7
        assert hook["args"] == ["-m", self.SS, "--env", "CLAUDE_INVOKED_BY=engram_session_start",
                                "--env", f"ENGRAM_DIR={custom.resolve().as_posix()}"]
        assert _root_in_hook_process(hook, auto_inject_resume_brief, monkeypatch) == custom.resolve()

    def test_owner_script_is_left_alone(self, home, claude_config, tmp_path):
        custom = self._custom(tmp_path)
        written = {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command", "command": "my-wrapper.sh auto_inject_resume_brief"}]}]}}
        _write(claude_config, written)

        assert W._inject_claude_code_sessionstart_hook(
            PY, file_safety_root=custom, authorized_external_write=True) is None
        assert _read(claude_config) == written


class TestDoctor:
    @pytest.fixture
    def run_doctor(self, home, claude_config, tmp_path, monkeypatch, capsys):
        custom = tmp_path / "engram-root"
        monkeypatch.setenv("ENGRAM_DIR", str(custom))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(W, "_find_python", lambda: PY)

        def run(*, fix: bool) -> str:
            monkeypatch.setenv("ENGRAM_DIR", str(custom))
            capsys.readouterr()
            doctor._run_functional_checks(fix=fix)
            return capsys.readouterr().out

        run.custom = custom
        return run

    def test_flags_then_fixes_a_hook_without_the_data_dir(self, run_doctor, claude_config, monkeypatch):
        ss = "piia_engram.hooks.auto_inject_resume_brief"
        _write(claude_config, {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command",
             "command": f"{PY} -m {ss} --env CLAUDE_INVOKED_BY=engram_session_start"}]}]}})

        out = run_doctor(fix=False)
        assert "[!!] SessionStart (resume brief inject) hook does not name the data directory" in out
        assert "[ok] SessionStart (resume brief inject) hook registered" not in out

        out = run_doctor(fix=True)
        assert "[fixed] SessionStart (resume brief inject) hook rewritten in place" in out
        (hook,) = _hooks(_read(claude_config), "SessionStart")
        root = _root_in_hook_process(hook, auto_inject_resume_brief, monkeypatch)
        assert root == run_doctor.custom.resolve()

        out = run_doctor(fix=False)
        assert "[ok] SessionStart (resume brief inject) hook registered" in out
        assert "does not name the data directory" not in out

    def test_default_location_is_not_flagged(self, run_doctor, home, claude_config, monkeypatch, capsys):
        ss = "piia_engram.hooks.auto_inject_resume_brief"
        _write(claude_config, {"hooks": {"SessionStart": [{"hooks": [
            {"type": "command",
             "command": f"{PY} -m {ss} --env CLAUDE_INVOKED_BY=engram_session_start"}]}]}})
        monkeypatch.setenv("ENGRAM_DIR", str(home / ".engram"))
        capsys.readouterr()
        doctor._run_functional_checks(fix=False)
        out = capsys.readouterr().out
        assert "[ok] SessionStart (resume brief inject) hook registered" in out
        assert "does not name the data directory" not in out
