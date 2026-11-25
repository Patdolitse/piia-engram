"""A machine-wide ``ENGRAM_DIR`` reaches hooks without being named.

On Windows a variable set for the account or the machine (System
Properties, ``setx``) is in the environment of every new process, Claude
Code and its hooks included. Doctor used to report "hook does not name the
data directory" for such a setup although the hooks opened the right
store. It now reads the two registry keys that hold those variables and
reports nothing when ``ENGRAM_DIR`` there is the data directory. A
directory set only in the MCP entry is still reported.

No test reads the real registry: ``conftest`` stubs ``persistent_env.read``
and the reader itself is exercised with a stand-in for ``winreg``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import doctor, persistent_env

PY = "/usr/bin/python3"
SS = "piia_engram.hooks.auto_inject_resume_brief"
OLD_HOOK = {"type": "command",
            "command": f"{PY} -m {SS} --env CLAUDE_INVOKED_BY=engram_session_start"}
LABEL = "SessionStart (resume brief inject)"
MACHINE_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


class FakeRegistry:
    """Stand-in for ``winreg`` holding {(hive, key): {name: (value, kind)}}."""

    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = "HKCU", "HKLM"
    REG_SZ, REG_EXPAND_SZ = 1, 2

    def __init__(self, values: dict, *, unopenable: tuple = ()):
        self.values, self.unopenable, self.opened = values, unopenable, []

    class _Key:
        def __init__(self, names):
            self.names = names

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, hive, key):
        self.opened.append((hive, key))
        if hive in self.unopenable:
            raise PermissionError(hive)
        return self._Key(self.values.get((hive, key), {}))

    def QueryValueEx(self, handle, name):
        if name not in handle.names:
            raise FileNotFoundError(name)
        return handle.names[name]

    def ExpandEnvironmentStrings(self, value):
        return value.replace("%DATA%", r"D:\data")


class TestRegistryReader:
    def test_account_value_wins_over_the_machine_value(self):
        reg = FakeRegistry({
            ("HKCU", "Environment"): {"ENGRAM_DIR": (r"D:\mine", 1)},
            ("HKLM", MACHINE_KEY): {"ENGRAM_DIR": (r"D:\everyone", 1)},
        })
        assert persistent_env._read_from(reg, "ENGRAM_DIR") == r"D:\mine"

    def test_machine_value_is_the_fallback(self):
        reg = FakeRegistry({("HKLM", MACHINE_KEY): {"ENGRAM_DIR": (r"D:\everyone", 1)}})
        assert persistent_env._read_from(reg, "ENGRAM_DIR") == r"D:\everyone"
        assert reg.opened == [("HKCU", "Environment"), ("HKLM", MACHINE_KEY)]

    def test_expandable_value_is_expanded(self):
        reg = FakeRegistry({("HKCU", "Environment"): {"ENGRAM_DIR": (r"%DATA%\engram", 2)}})
        assert persistent_env._read_from(reg, "ENGRAM_DIR") == r"D:\data\engram"

    @pytest.mark.parametrize("stored", [("", 1), ("   ", 1), (7, 4)])
    def test_blank_or_non_text_value_is_no_value(self, stored):
        reg = FakeRegistry({("HKCU", "Environment"): {"ENGRAM_DIR": stored}})
        assert persistent_env._read_from(reg, "ENGRAM_DIR") is None

    def test_unset_or_unreadable_is_no_value(self):
        assert persistent_env._read_from(FakeRegistry({}), "ENGRAM_DIR") is None
        locked = FakeRegistry({("HKCU", "Environment"): {"ENGRAM_DIR": (r"D:\mine", 1)}},
                              unopenable=("HKCU", "HKLM"))
        assert persistent_env._read_from(locked, "ENGRAM_DIR") is None

    def test_other_platforms_have_no_such_store(self):
        assert persistent_env._read("ENGRAM_DIR", windows=False) is None


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def machine_env(monkeypatch):
    def install(value: str | None):
        monkeypatch.setattr(
            persistent_env, "read", lambda name: value if name == "ENGRAM_DIR" else None)

    return install


class TestHookDataDir:
    def test_machine_wide_directory_needs_no_argument(self, home, tmp_path, machine_env):
        custom = tmp_path / "store"
        custom.mkdir()
        machine_env(None)
        assert W._hook_data_dir(custom) == custom.resolve().as_posix()

        machine_env(str(custom))

        assert W._hook_data_dir(custom) is None
        assert not W._hook_lacks_data_dir(OLD_HOOK, W._hook_data_dir(custom))

    def test_directory_set_only_in_the_mcp_entry_is_still_named(self, home, tmp_path, machine_env):
        """The real failure: nothing machine-wide, or something else machine-wide."""
        custom, other = tmp_path / "store", tmp_path / "other"
        custom.mkdir()
        other.mkdir()
        for value in (None, str(other), ""):
            machine_env(value)
            wanted = W._hook_data_dir(custom)
            assert wanted == custom.resolve().as_posix()
            assert W._hook_lacks_data_dir(OLD_HOOK, wanted)

    def test_default_location_is_named_when_the_machine_points_elsewhere(self, home, tmp_path, machine_env):
        """A hook without the argument would follow the machine-wide variable."""
        default = home / ".engram"
        default.mkdir()
        machine_env(str(tmp_path / "other"))
        assert W._hook_data_dir(default) == default.resolve().as_posix()

    def test_a_failing_read_changes_nothing(self, home, tmp_path, monkeypatch):
        custom = tmp_path / "store"
        custom.mkdir()

        def boom(name):
            raise OSError("registry unavailable")

        monkeypatch.setattr(persistent_env, "read", boom)
        assert W._hook_data_dir(custom) == custom.resolve().as_posix()


class TestDoctor:
    @pytest.fixture
    def run_doctor(self, home, tmp_path, monkeypatch, capsys):
        config = tmp_path / "claude-config"
        config.mkdir()
        custom = tmp_path / "engram-root"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        monkeypatch.setenv("ENGRAM_DIR", str(custom))
        monkeypatch.setenv("ENGRAM_TEST", "1")
        monkeypatch.setattr(W, "_find_python", lambda: PY)
        (config / "settings.json").write_text(
            json.dumps({"hooks": {"SessionStart": [{"hooks": [dict(OLD_HOOK)]}]}}),
            encoding="utf-8")

        def run() -> str:
            capsys.readouterr()
            doctor._run_functional_checks(fix=False)
            return capsys.readouterr().out

        run.custom = custom
        return run

    def test_no_false_alarm_with_a_machine_wide_directory(self, run_doctor, machine_env):
        machine_env(str(run_doctor.custom))
        out = run_doctor()
        assert f"[ok] {LABEL} hook registered" in out
        assert "does not name the data directory" not in out

    def test_still_reported_without_one(self, run_doctor, machine_env):
        machine_env(None)
        out = run_doctor()
        assert f"[!!] {LABEL} hook does not name the data directory" in out
