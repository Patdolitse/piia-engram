"""Real, bounded MCP help startup is tested separately from doctor display."""

from __future__ import annotations

import sys
from piia_engram import setup_wizard as W


def test_real_mcp_help_startup_in_an_isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
    store = tmp_path / "probe-store"
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    issue = W._probe_mcp_entry({
        "command": sys.executable, "args": ["-m", "piia_engram.mcp_server"],
    })
    assert issue is None
    assert not store.exists()


def test_real_startup_process_timeout(tmp_path, monkeypatch):
    # Exercise actual subprocess timeout handling without making MCP startup
    # timing part of the display contract or extending the default five seconds.
    package = tmp_path / "piia_engram"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mcp_server.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    issue = W._probe_mcp_entry({
        "command": sys.executable, "args": ["-m", "piia_engram.mcp_server"],
    }, timeout=1)
    assert issue == "MCP launch probe timed out after 1s"
