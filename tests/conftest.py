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
