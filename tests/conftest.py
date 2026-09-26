"""Shared pytest fixtures for the Engram test suite."""

import os
import tempfile
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
os.environ["ENGRAM_DIR"] = str(
    Path(tempfile.mkdtemp(prefix="engram-collect-")) / "engram-home"
)
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
