"""Shared pytest fixtures for the Engram test suite."""

import os

import pytest


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
