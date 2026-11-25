"""Doctor checks hooks against the data directory Claude Code really uses.

An owner with a custom data directory often has it in one place only: the
``env`` of the Engram MCP entry in Claude Code's user config. A terminal
has no ``ENGRAM_DIR`` then, so ``engram doctor`` opens the default store,
and it used to judge the hooks against that one: hooks that would read the
wrong store were reported healthy and ``--fix`` left them. Doctor now reads
the entry (read-only) and checks the hooks against its directory, and says
so when that is not the directory of the run.

Every config file here is synthetic.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import claude_code_mcp, doctor

PY = "/usr/bin/python3"
SS = "piia_engram.hooks.auto_inject_resume_brief"
OLD_HOOK = {"type": "command",
            "command": f"{PY} -m {SS} --env CLAUDE_INVOKED_BY=engram_session_start"}
LABEL = "SessionStart (resume brief inject)"


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "claude-config"
    config.mkdir()
    custom = tmp_path / "custom store"
    custom.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setattr(W, "_find_python", lambda: PY)
    # The terminal of an owner who never set ENGRAM_DIR outside the MCP entry.
    # (setenv first: delenv records nothing for a variable that is not set.)
    monkeypatch.setenv("ENGRAM_DIR", "recorded-for-restore")
    monkeypatch.delenv("ENGRAM_DIR")

    class World:
        settings = config / "settings.json"
        user_config = claude_code_mcp.user_config_path()

        def entry(self, env: dict | None, name: str = "engram") -> None:
            server = {"command": PY, "args": ["-m", "piia_engram.mcp_server"]}
            if env is not None:
                server["env"] = env
            self.user_config.write_text(
                json.dumps({"mcpServers": {"other": {"command": "x"}, name: server}}),
                encoding="utf-8")

        def hooks(self, *hooks: dict) -> None:
            self.settings.write_text(
                json.dumps({"hooks": {"SessionStart": [{"hooks": list(hooks)}]}}),
                encoding="utf-8")

        def hook(self) -> dict:
            data = json.loads(self.settings.read_text(encoding="utf-8"))
            (hook,) = data["hooks"]["SessionStart"][0]["hooks"]
            return hook

        def doctor(self, *, fix: bool) -> str:
            capsys.readouterr()
            doctor._run_functional_checks(fix=fix)
            return capsys.readouterr().out

    world = World()
    world.home, world.custom = home, custom
    return world


def _named_dir(hook: dict) -> str | None:
    argv = hook["args"] if isinstance(hook.get("args"), list) else shlex.split(hook["command"])
    for index, arg in enumerate(argv[:-1]):
        if arg == "--env" and argv[index + 1].startswith("ENGRAM_DIR="):
            return argv[index + 1].partition("=")[2]
    return None


class TestEntryReader:
    def test_reads_the_user_scope_entry(self, world):
        world.entry({"ENGRAM_DIR": str(world.custom), "ENGRAM_TOOLS": "all"})
        assert claude_code_mcp.engram_entry_data_dir() == str(world.custom)

    def test_the_readme_name_counts(self, world):
        world.entry({"ENGRAM_DIR": str(world.custom)}, name="piia-engram")
        assert claude_code_mcp.engram_entry_data_dir() == str(world.custom)

    @pytest.mark.parametrize("env", [None, {}, {"ENGRAM_DIR": ""}, {"ENGRAM_DIR": 3},
                                     {"ENGRAM_TOOLS": "all"}])
    def test_no_directory_in_the_entry(self, world, env):
        world.entry(env)
        assert claude_code_mcp.engram_entry_data_dir() is None

    def test_missing_or_broken_file(self, world):
        assert claude_code_mcp.engram_entry_data_dir() is None
        world.user_config.write_text("{not json", encoding="utf-8")
        assert claude_code_mcp.engram_entry_data_dir() is None

    def test_reading_changes_nothing(self, world):
        world.entry({"ENGRAM_DIR": str(world.custom)})
        before = world.user_config.read_bytes()
        mtime = world.user_config.stat().st_mtime_ns
        claude_code_mcp.engram_entry_data_dir()
        assert world.user_config.read_bytes() == before
        assert world.user_config.stat().st_mtime_ns == mtime


class TestDoctor:
    def test_hook_for_the_wrong_store_is_reported_and_fixed(self, world):
        """The reported miss: directory only in the MCP entry, hooks without it."""
        world.entry({"ENGRAM_DIR": str(world.custom)})
        world.hooks(dict(OLD_HOOK))
        user_config_before = world.user_config.read_bytes()

        out = world.doctor(fix=False)

        assert f"[!!] {LABEL} hook does not name the data directory" in out
        assert f"[ok] {LABEL} hook registered" not in out
        # Doctor says which directory it judged the hooks against, and why.
        assert "[--] ENGRAM_DIR is not set in this terminal" in out
        assert str(world.custom.resolve()) in out
        assert world.hook() == OLD_HOOK  # read-only without --fix

        out = world.doctor(fix=True)

        assert f"[fixed] {LABEL} hook rewritten in place" in out
        assert _named_dir(world.hook()) == world.custom.resolve().as_posix()
        assert world.user_config.read_bytes() == user_config_before  # never written
        assert "ENGRAM_DIR" not in os.environ

        out = world.doctor(fix=False)
        assert f"[ok] {LABEL} hook registered" in out
        assert "does not name the data directory" not in out

    def test_new_hooks_get_the_entry_directory(self, world):
        world.entry({"ENGRAM_DIR": str(world.custom)})

        world.doctor(fix=True)

        assert _named_dir(world.hook()) == world.custom.resolve().as_posix()

    def test_entry_and_run_agree(self, world, monkeypatch):
        world.entry({"ENGRAM_DIR": str(world.custom)})
        world.hooks(dict(OLD_HOOK))
        monkeypatch.setenv("ENGRAM_DIR", str(world.custom))

        out = world.doctor(fix=False)

        assert f"[!!] {LABEL} hook does not name the data directory" in out
        assert "is not set in this terminal" not in out
        assert "Data directory mismatch" not in out

    def test_entry_and_run_disagree_is_said_plainly(self, world, monkeypatch, tmp_path):
        other = tmp_path / "other store"
        other.mkdir()
        world.entry({"ENGRAM_DIR": str(world.custom)})
        world.hooks(dict(OLD_HOOK))
        monkeypatch.setenv("ENGRAM_DIR", str(other))

        out = world.doctor(fix=False)

        assert "[!!] Data directory mismatch" in out
        assert str(other.resolve()) in out and str(world.custom.resolve()) in out

        world.doctor(fix=True)

        # Hooks follow the directory Claude Code's own Engram entry uses, never
        # the directory of whichever terminal ran doctor.
        assert _named_dir(world.hook()) == world.custom.resolve().as_posix()

    def test_entry_without_a_directory_changes_nothing(self, world):
        world.entry({"ENGRAM_TOOLS": "all"})
        world.hooks(dict(OLD_HOOK))

        out = world.doctor(fix=False)

        assert f"[ok] {LABEL} hook registered" in out
        assert "is not set in this terminal" not in out

    def test_no_user_config_changes_nothing(self, world):
        world.hooks(dict(OLD_HOOK))

        out = world.doctor(fix=False)

        assert f"[ok] {LABEL} hook registered" in out
        assert "is not set in this terminal" not in out


def test_setup_offers_the_entry_directory_first(world, monkeypatch):
    """Without ENGRAM_DIR in the terminal, setup starts from the directory the
    MCP entry already uses instead of the default one."""
    world.entry({"ENGRAM_DIR": str(world.custom)})
    assert W._default_data_dir() == str(world.custom)

    monkeypatch.setenv("ENGRAM_DIR", str(world.home / "elsewhere"))
    assert W._default_data_dir() == str(world.home / "elsewhere")

    monkeypatch.delenv("ENGRAM_DIR")
    world.user_config.unlink()
    assert W._default_data_dir() == str(world.home / ".engram")
