"""demos/setup_engram.py points Claude Code users at `claude mcp add`."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_DEMO = Path(__file__).resolve().parent.parent / "demos" / "setup_engram.py"


def _demo():
    spec = importlib.util.spec_from_file_location("setup_engram_demo", _DEMO)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_reads_the_claude_user_config(tmp_path, capsys):
    demo = _demo()
    config = tmp_path / ".claude.json"
    config.write_text(json.dumps({"projects": {"/p": {"mcpServers": {"piia-engram": {"command": "x"}}}}}),
                      encoding="utf-8")
    assert demo.check_mcp_config(config, tmp_path) is True


def test_demo_prints_the_claude_mcp_add_command(tmp_path, capsys):
    demo = _demo()
    assert demo.check_mcp_config(tmp_path / ".claude.json", tmp_path) is False
    out = capsys.readouterr().out
    assert "claude mcp add --scope user engram -- piia-engram-mcp" in out
    assert ".mcp.json" not in out


def test_demo_default_location_follows_claude_config_dir(tmp_path, monkeypatch):
    demo = _demo()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert demo.claude_user_config() == tmp_path / "cfg" / ".claude.json"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert demo.claude_user_config() == Path.home() / ".claude.json"
