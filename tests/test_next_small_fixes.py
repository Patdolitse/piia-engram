"""Setup, stats privacy and client activity regressions on temporary stores."""
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from piia_engram import setup_wizard as W, stats, mcp_server as M
from piia_engram import connection_report as C, write_provenance as P
from piia_engram.core import Engram


@pytest.mark.parametrize("key", ["piia-engram", '"piia-engram"', "engram"])
def test_codex_inline_table_migration_preserves_env_and_backup(tmp_path, key):
    config = tmp_path / "config.toml"
    original = ('model = "example"\n[mcp_servers]\n'
                f'{key} = {{ command = "python", args = ["-m", "piia_engram.mcp_server"], '
                'env = { ENGRAM_APPROVAL = "strict", CUSTOM_VALUE = "keep # this", ENGRAM_DIR = "store" } } # user\n'
                'other = { command = "other", args = [] }\n[features]\npreview = true\n')
    config.write_text(original, encoding="utf-8")
    W._write_mcp_config_toml(config, sys.executable, "server.py")
    parsed = W._parse_toml(config.read_text(encoding="utf-8"))
    assert "piia-engram" not in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["engram"]["env"]["CUSTOM_VALUE"] == "keep # this"
    assert parsed["mcp_servers"]["engram"]["env"]["ENGRAM_APPROVAL"] == "strict"
    assert parsed["mcp_servers"]["engram"]["env"]["ENGRAM_DIR"] == "store"
    assert parsed["mcp_servers"]["other"]["command"] == "other"
    assert parsed["features"]["preview"] is True
    assert next(tmp_path.glob("config.toml.engram-backup.*")).read_text(encoding="utf-8") == original
    W._write_mcp_config_toml(config, sys.executable, "server.py")
    assert len(W._parse_toml(config.read_text())["mcp_servers"]) == 2


def test_setup_restart_hint_once(tmp_path, monkeypatch, capsys):
    from piia_engram import i18n
    # run_setup deliberately changes these globals; restore them at test teardown.
    monkeypatch.setattr(W, "_lang", W._lang)
    monkeypatch.setattr(i18n, "_runtime_lang", i18n._runtime_lang)
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setattr(W, "_choose_data_dir", lambda d: d)
    monkeypatch.setattr(W, "_find_python", lambda: sys.executable)
    monkeypatch.setattr(W, "_find_mcp_server", lambda: str(Path(W.__file__).with_name("mcp_server.py")))
    monkeypatch.setattr(W, "_probe_environment", lambda **kw: {})
    monkeypatch.setattr(W, "_scan_rule_files", lambda **kw: [])
    monkeypatch.setattr(W, "_prompt", lambda *args, **kw: "2")
    monkeypatch.setattr(W, "_choice", lambda *args, **kw: "")
    monkeypatch.setattr(W, "_yn", lambda *args, **kw: False)
    monkeypatch.setattr(W, "_run_hybrid_search_offer", lambda *args: False)
    monkeypatch.setattr(W, "_run_privacy_defaults", lambda *args, **kw: None)
    monkeypatch.setattr(W, "_detect_tools", lambda: [{"name": "Claude Code"}])
    monkeypatch.setattr(W, "_apply_external_configs", lambda *args, **kw: (["Claude Code"], [], []))
    monkeypatch.setattr(W, "_print_restart_hints", lambda *args: print("Close and reopen your VS Code terminal"))
    W.run_setup(apply_external_config=True)
    assert capsys.readouterr().out.count("Close and reopen your VS Code terminal") == 1


@pytest.mark.parametrize("flag,value", [("DO_NOT_TRACK", "1"), ("ENGRAM_TELEMETRY", "0")])
@pytest.mark.parametrize("operation", ["show", "log"])
def test_stats_opt_out_zero_network(monkeypatch, flag, value, operation):
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
    monkeypatch.setenv(flag, value)
    gh, pypi = Mock(return_value=None), Mock(return_value=None)
    monkeypatch.setattr(stats, "_gh", gh)
    monkeypatch.setattr(stats, "_pypi_recent", pypi)
    (stats.run_stats if operation == "show" else stats.log_stats)()
    gh.assert_not_called()
    pypi.assert_not_called()


def test_stats_online_explicit_override_and_help(monkeypatch, capsys):
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    gh, pypi = Mock(return_value=None), Mock(return_value=None)
    monkeypatch.setattr(stats, "_gh", gh)
    monkeypatch.setattr(stats, "_pypi_recent", pypi)
    stats.run_stats(online=True)
    assert gh.called and pypi.called
    gh.reset_mock(); pypi.reset_mock()
    monkeypatch.setattr(sys, "argv", ["engram-stats", "--help"])
    stats.main()
    assert "--online" in capsys.readouterr().out
    gh.assert_not_called(); pypi.assert_not_called()


@pytest.mark.parametrize("name", ["claude-code", "claude_code", "Claude Code", "claude-code/2.1", "claude-cli"])
def test_claude_code_handshake_and_provenance_share_label(tmp_path, monkeypatch, name):
    monkeypatch.setenv("ENGRAM_HEARTBEAT_INTERVAL", "0")
    tracker = M._SessionTracker()
    tracker.detect_client_info(name, "2.1")
    assert tracker.tool_name == "claude_code"
    assert P.client_label(name) == "claude_code"
    root = tmp_path / "store"
    eng = Engram(root=root)
    path = root / "contexts" / tracker.tool_name / "auto-2026-10-08-cp1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("工具调用次数: 3\n", encoding="utf-8")
    with P.origin_scope("mcp", client_name=name):
        eng.add_lesson("Cache generated assets after a successful build")
    activity = C.call_activity(root)
    assert activity["claude_code"]["calls"] == 3
    assert activity["claude_code"]["writes"] == 1


def test_doctor_recognises_legacy_claude_cli_checkpoint(tmp_path):
    path = tmp_path / "contexts" / "claude_cli" / "auto-2026-10-08.md"
    path.parent.mkdir(parents=True)
    path.write_text("工具调用次数: 7\n", encoding="utf-8")
    assert C.call_activity(tmp_path)["claude_code"]["calls"] == 7


def test_doctor_relabels_legacy_other_provenance_without_exposing_name(tmp_path):
    from datetime import datetime, timezone
    path = tmp_path / "knowledge" / "lessons.json"
    path.parent.mkdir()
    path.write_text(json.dumps([{"created_at": datetime.now(timezone.utc).isoformat(),
                                 "provenance": {"origin": "mcp", "client": "other",
                                                "client_name": "claude_cli"}}]), encoding="utf-8")
    assert C.call_activity(tmp_path)["claude_code"]["writes"] == 1
