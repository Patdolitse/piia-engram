"""Shared pytest fixtures for the Engram test suite."""

import atexit
import os
import shutil
import tempfile
import threading
from pathlib import Path

import pytest

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")

# Collection-time seal: importing some test modules imports
# piia_engram.mcp_server, which constructs Engram() at module import time —
# during pytest COLLECTION, before any fixture (including the isolation
# fixtures below) can run. With a machine-wide ENGRAM_DIR exported, that init
# writes into the developer's LIVE store (session_state stamp, structure
# creation, file-safety ledger appends — and log rotation once the store
# self-healing code runs). Fixtures cannot guard imports; only module-level
# conftest code runs early enough, because pytest imports conftest.py before
# collecting any test module.
os.environ["ENGRAM_TEST"] = "1"
# Removed again when the session ends (pytest_sessionfinish, atexit as a
# fallback), so runs do not pile up engram-collect-* directories in the
# temp dir. It has to exist before any fixture, so it cannot come from
# tmp_path_factory.
_COLLECT_DIR = Path(tempfile.mkdtemp(prefix="engram-collect-"))
os.environ["ENGRAM_DIR"] = str(_COLLECT_DIR / "engram-home")
# The update-check cache lives outside the store (4.21.2), in the user's cache
# directory by default; the suite must never write there either.
os.environ["ENGRAM_CACHE_DIR"] = str(Path(os.environ["ENGRAM_DIR"]).parent / "engram-cache")
os.environ["ENGRAM_NO_UPDATE_CHECK"] = "1"

# Machine-wide behaviour switches (a developer box may export
# ENGRAM_APPROVAL=strict, ENGRAM_RECONCILE=0, ...) must not leak into the suite:
# mcp_server reads some of them at import time. Tests that need one set it
# themselves with monkeypatch.
_MACHINE_BEHAVIOUR_ENV = (
    "ENGRAM_APPROVAL",
    "ENGRAM_RECONCILE",
    "ENGRAM_GOVERNANCE",
    "ENGRAM_CLIENT_TYPE",
    "ENGRAM_MCP_STARTUP_SYNC",
    # capacity limits (4.21.x); a developer box may set them for its live store
    "ENGRAM_CAP_SOFT",
    "ENGRAM_CAP_HARD",
    "ENGRAM_REVIEW_QUEUE_MAX",
    "ENGRAM_REVIEW_QUEUE_CEILING",
    "ENGRAM_REVIEW_MIN_STAY_DAYS",
    "ENGRAM_RETIRED_GRACE_DAYS",
    "ENGRAM_RETIRED_MAX",
    "ENGRAM_PLAYBOOK_QUEUE_MAX",
)
for _name in _MACHINE_BEHAVIOUR_ENV:
    os.environ.pop(_name, None)

# The daily usage ping keeps its state (install id, settings, notice marker) in
# the per-user config dir, outside any store. Remember where that is for the
# real profile BEFORE any fixture moves it, so a guard test can prove the suite
# never creates or writes it.
_PING_PROFILE_ENV = ("APPDATA", "XDG_CONFIG_HOME", "HOME", "USERPROFILE")
REAL_PROFILE_ENV = {name: os.environ.get(name) for name in _PING_PROFILE_ENV}
from piia_engram import usage_ping as _usage_ping  # noqa: E402  (after the env seal above)

try:
    REAL_PING_STATE_DIR = _usage_ping.state_dir()
except Exception:  # no resolvable home: nothing real to protect
    REAL_PING_STATE_DIR = None


def _remove_collect_dir(path: Path = _COLLECT_DIR) -> None:
    shutil.rmtree(path, ignore_errors=True)


atexit.register(_remove_collect_dir)


def pytest_sessionfinish(session, exitstatus) -> None:
    """Drop the collection-time store made above (see _COLLECT_DIR)."""
    _remove_collect_dir()


@pytest.fixture(scope="session", autouse=True)
def _subprocess_pythonpath() -> None:
    """Ensure subprocesses can import piia_engram even without pip install.

    pytest's ``pythonpath = ["src"]`` only adds to sys.path inside the test
    process.  Subprocesses (e.g. the MCP launch probe in doctor, the atexit
    integration test) inherit the *environment*, not sys.path — so they need
    PYTHONPATH set.
    """
    existing = os.environ.get("PYTHONPATH", "")
    if _SRC_DIR not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            f"{_SRC_DIR}{os.pathsep}{existing}" if existing else _SRC_DIR
        )


@pytest.fixture(scope="session", autouse=True)
def _isolate_engram_store_session(tmp_path_factory) -> None:
    """Session baseline: move ENGRAM_DIR off the real store before ANY fixture.

    Module/session-scoped fixtures instantiate before the function-scoped
    isolation below, so they would otherwise see the inherited ENGRAM_DIR (the
    developer's active store). This sets a session-wide throwaway dir as the
    floor; the per-test fixture then narrows it to each test's own dir.
    """
    base = tmp_path_factory.mktemp("engram-session")
    os.environ["ENGRAM_TEST"] = "1"
    os.environ["ENGRAM_DIR"] = str(base / "engram-home")
    os.environ["ENGRAM_CACHE_DIR"] = str(base / "engram-cache")
    # No session-scoped fixture sees the real home either (see _isolate_home).
    session_home = base / "isolated-home"
    session_home.mkdir(exist_ok=True)
    os.environ["HOME"] = str(session_home)
    os.environ["USERPROFILE"] = str(session_home)


@pytest.fixture(autouse=True)
def _isolate_engram_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Hard-isolate every test from the developer's real Engram store.

    The store root resolves as ``ENGRAM_DIR`` (or ``Path.home()/.engram`` as a
    fallback), and on a real machine ENGRAM_DIR is exported to the *active* store
    (and ~/.engram can be a symlink to it). Without this, a test that forgets to
    set its own root could read stale state from — or worse, write into — the
    user's real memory.

    So point ENGRAM_DIR at a per-test throwaway dir before each test. Tests that
    need a specific dir just set ENGRAM_DIR/delenv themselves (theirs runs after
    this and wins); tests/test_store_isolation.py proves the default holds.

    ``ENGRAM_TEST=1`` keeps audit logging and the DATA FRAGMENTATION warning off
    (suite isolation) — core.py reads it for those carve-outs.
    """
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram-home"))
    monkeypatch.setenv("ENGRAM_CACHE_DIR", str(tmp_path / "engram-cache"))
    # No update check (no network, no cache write) unless a test turns it back on.
    monkeypatch.setenv("ENGRAM_NO_UPDATE_CHECK", "1")
    for name in _MACHINE_BEHAVIOUR_ENV:
        monkeypatch.delenv(name, raising=False)
    # Usage ping state (written by e.g. `engram telemetry on/off`) goes to a
    # throwaway dir, never the real %APPDATA% / ~/.config / ~/Library profile.
    # The env covers subprocesses; the patch covers macOS, which uses Path.home().
    ping_profile = tmp_path / "ping-profile"
    monkeypatch.setenv("APPDATA", str(ping_profile / "AppData" / "Roaming"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(ping_profile / ".config"))
    monkeypatch.setattr(_usage_ping, "state_dir", lambda: ping_profile / "piia-engram")
    # HOME / USERPROFILE point at an empty per-test directory, so no test reads
    # another AI tool's real files (or anything else) under the real home.
    # Tests that need such files use the `other_ai_tools_home` fixture.
    # A sibling of tmp_path, so tests that expect an empty tmp_path still get one.
    isolated_home = tmp_path.parent / f"{tmp_path.name}-isolated-home"
    isolated_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("USERPROFILE", str(isolated_home))
    # Claude Code: never the real config dir, and never the real `claude`
    # command (it would edit the real ~/.claude.json). Tests that exercise the
    # command replace these seams with a recorder or a fake executable.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(isolated_home / ".claude"))
    monkeypatch.setenv("LOCALAPPDATA", str(ping_profile / "AppData" / "Local"))
    from piia_engram import claude_code_mcp as _claude_code_mcp

    def _no_real_claude(*_args, **_kwargs):
        raise AssertionError("tests must not run the real claude command")

    original_override = _claude_code_mcp.config_dir_override
    baseline_override = str(isolated_home / ".claude")
    # Default-layout tests exercise fallback under their own isolated HOME.
    # Keep the explicit baseline env for subprocesses, while honoring any
    # test that deliberately supplies a distinct override directory.
    monkeypatch.setattr(_claude_code_mcp, "config_dir_override", lambda:
        None if os.environ.get("CLAUDE_CONFIG_DIR") == baseline_override else original_override())
    monkeypatch.setattr(_claude_code_mcp, "cli_path", lambda: None)
    monkeypatch.setattr(_claude_code_mcp, "run_cli", _no_real_claude)
    try:
        yield
    finally:
        # Stop test-owned sessions before the next test changes the store root.
        # Keep heartbeat behaviour enabled during tests that exercise it.
        heartbeats = []
        for thread in threading.enumerate():
            if thread.name != "engram-heartbeat":
                continue
            target = getattr(thread, "_target", None)
            tracker = getattr(target, "__self__", None)
            if getattr(tracker, "_heartbeat_thread", None) is not thread:
                continue
            tracker._stop_event.set()
            heartbeats.append(thread)
        for thread in heartbeats:
            thread.join(timeout=3.0)
            assert not thread.is_alive(), "test session heartbeat did not stop"


@pytest.fixture
def real_ping_state_dir():
    """Where the usage ping state lives in the real profile (None if unresolvable)."""
    return REAL_PING_STATE_DIR


def write_other_ai_tools_samples(home: Path) -> None:
    """A fake home holding other AI tools' memory and rule files."""
    # Claude Code auto-memory. The project dir name does not decode to a real
    # path, so project discovery never walks a real drive.
    mem = home / ".claude" / "projects" / "demo-project" / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("- [lint](lint_rule.md)\n", encoding="utf-8")
    (mem / "lint_rule.md").write_text(
        "---\nname: lint\ndescription: Always run the linter before committing code\n"
        "type: feedback\n---\n\nAlways run the linter before committing code; "
        "CI rejects unlinted pushes.\n",
        encoding="utf-8",
    )
    (mem / "deploy_note.md").write_text(
        "---\nname: deploy\ndescription: Deploy previews go to the staging bucket first\n"
        "type: project\n---\n\nDeploy previews go to the staging bucket first, "
        "never straight to production.\n",
        encoding="utf-8",
    )
    (home / ".claude" / "CLAUDE.md").write_text(
        "# Global rules\n\n## Language\nAll communication with me happens in English, "
        "including commit messages.\n\n## Reviews\nNever merge a pull request without "
        "a second human review of the diff.\n",
        encoding="utf-8",
    )
    codex = home / ".codex"
    codex.mkdir()
    (codex / "AGENTS.md").write_text(
        "# Agent rules\nI am a backend developer and prefer concise answers.\n"
        "Always write tests before changing behaviour.\n",
        encoding="utf-8",
    )
    rules = home / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "style.mdc").write_text(
        "Prefer small pure functions over classes when either would work.\n"
        "Keep modules under five hundred lines where practical.\n",
        encoding="utf-8",
    )


@pytest.fixture
def other_ai_tools_home(tmp_path, monkeypatch) -> Path:
    """HOME / USERPROFILE moved to a temp dir with other AI tools' sample files."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    write_other_ai_tools_samples(home)
    assert Path.home() == home
    return home
