"""Tests for MCP server degraded-mode when Engram init fails.

Codex HIGH-2: if schema_version.json or trust_boundaries.json is corrupted,
Engram() raises DataCorruptionError at module import time, crashing the MCP
server before main() runs.  The fix: wrap init, start in degraded mode, and
return clear error messages instead of dying.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _init_engram() — the extracted, testable init wrapper
# ---------------------------------------------------------------------------


def test_init_engram_returns_instance_on_success(tmp_path):
    """Happy path: _init_engram() returns (Engram, None)."""
    from piia_engram.mcp_server import _init_engram

    engram, err = _init_engram(root=tmp_path)
    assert engram is not None
    assert err is None


def test_init_engram_returns_none_on_data_corruption(tmp_path):
    """DataCorruptionError during init → (None, error_string)."""
    from piia_engram.storage import DataCorruptionError
    from piia_engram.mcp_server import _init_engram

    with patch(
        "piia_engram.core.Engram.__init__",
        side_effect=DataCorruptionError("schema_version.json: invalid JSON"),
    ):
        engram, err = _init_engram(root=tmp_path)

    assert engram is None
    assert "schema_version.json" in err


def test_init_engram_returns_none_on_runtime_error(tmp_path):
    """RuntimeError during init (e.g. missing corpus_salt) → degraded."""
    from piia_engram.mcp_server import _init_engram

    with patch(
        "piia_engram.core.Engram.__init__",
        side_effect=RuntimeError(".corpus_salt is missing"),
    ):
        engram, err = _init_engram(root=tmp_path)

    assert engram is None
    assert "corpus_salt" in err


# ---------------------------------------------------------------------------
# _require_engram() — guard for tool handlers
# ---------------------------------------------------------------------------


def test_require_engram_returns_instance_when_healthy(tmp_path):
    """When _engram is alive, _require_engram() returns it."""
    from piia_engram.mcp_server import _require_engram
    from piia_engram.core import Engram

    e = Engram(root=tmp_path)
    result = _require_engram(_engram=e, _init_error=None)
    assert result is e


def test_require_engram_raises_on_degraded_mode():
    """When _engram is None with an init error, raise a clear RuntimeError."""
    from piia_engram.mcp_server import _require_engram

    with pytest.raises(RuntimeError, match="degraded"):
        _require_engram(_engram=None, _init_error="schema_version.json corrupted")


def test_require_engram_includes_original_error():
    """The RuntimeError message should include the original init error."""
    from piia_engram.mcp_server import _require_engram

    with pytest.raises(RuntimeError, match="schema_version.json corrupted"):
        _require_engram(_engram=None, _init_error="schema_version.json corrupted")


# ---------------------------------------------------------------------------
# _engram_clean_shutdown() — atexit handler must tolerate None
# ---------------------------------------------------------------------------


def test_clean_shutdown_tolerates_none_engram():
    """atexit handler must not crash when _engram is None (degraded mode)."""
    from piia_engram import mcp_server as S

    original_engram = S._engram
    original_session = S._session
    try:
        S._engram = None
        # Should not raise
        S._engram_clean_shutdown()
    finally:
        S._engram = original_engram
        S._session = original_session


def test_startup_sync_tolerates_none_engram():
    """Startup sync must not crash when _engram is None (degraded mode)."""
    from piia_engram import mcp_server as S

    original_engram = S._engram
    try:
        S._engram = None
        # Should not raise
        S._run_startup_sync()
    finally:
        S._engram = original_engram
