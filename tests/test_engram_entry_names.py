"""Engram entries under other names in non-Claude clients.

Every reader (doctor, its connection report, engram status, dock-governance,
the integrity report) recognises an Engram entry the same way: an ``engram``
or ``piia-engram`` key, or a server that launches Engram. setup migrates a
``piia-engram`` entry that launches Engram to the ``engram`` key (backed up
first, owner env kept) instead of adding a second entry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from piia_engram import setup_wizard as W  # noqa: F401  (import order: avoids a cycle)
from piia_engram import cli_commands, doctor
from piia_engram import connection_report as C
from piia_engram.status_report import _client_summary

_SECRET = "placeholder-owner-value"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    for var in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(h))
    monkeypatch.setenv("APPDATA", str(h / "AppData" / "Roaming"))
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    return h


def _write(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


ENTRIES = {
    "piia-engram": {"command": "piia-engram-mcp", "args": []},
    "memory": {"command": "uvx", "args": ["--from", "piia-engram", "piia-engram-mcp"]},
    "engram": {"command": sys.executable, "args": ["-m", "piia_engram.mcp_server"]},
}


@pytest.mark.parametrize("name", sorted(ENTRIES))
def test_every_reader_sees_an_engram_entry_under_any_name(home, tmp_path, name):
    _write(home / ".cursor" / "mcp.json", {"mcpServers": {name: ENTRIES[name], "other": {"command": "x"}}})

    tool = next(t for t in doctor._detect_installed_tools() if t["tool_id"] == "cursor")
    assert tool["status"] == "configured" and tool["engram_name"] == name
    row = next(r for r in C.build_report(tmp_path / "store", days=14)["clients"] if r["tool_id"] == "cursor")
    assert row["config_status"] == "configured"
    status_row = next(r for r in _client_summary()["tools"] if r["name"] == "Cursor")
    assert status_row["status"] in ("configured", "needs attention")
    dock_row = next(r for r in cli_commands._dock_config_governance_summary()["clients"] if r["name"] == "Cursor")
    assert dock_row["status"] in ("configured", "needs attention")
    integrity = next(r for r in doctor._build_config_integrity_report(cwd=tmp_path)["mcp_configs"]
                     if r["tool_id"] == "cursor")
    assert integrity["configured"] is True


def test_a_foreign_server_is_not_engram(home, tmp_path):
    _write(home / ".cursor" / "mcp.json", {"mcpServers": {"other": {"command": "npx", "args": ["x"]}}})
    tool = next(t for t in doctor._detect_installed_tools() if t["tool_id"] == "cursor")
    assert tool["status"] == "installed"


def test_doctor_validates_an_entry_under_another_name(home, tmp_path):
    _write(home / ".cursor" / "mcp.json", {"mcpServers": {"piia-engram": {
        "command": str(tmp_path / "missing" / "python.exe"), "args": ["-m", "piia_engram.mcp_server"]}}})
    tool = next(t for t in doctor._detect_installed_tools() if t["tool_id"] == "cursor")
    issues = doctor._validate_engram_entry(tool["servers"], tool["config_path"], name=tool["engram_name"])
    assert issues


def test_setup_migrates_piia_engram_to_the_engram_key(home, tmp_path, capsys):
    path = _write(home / ".cursor" / "mcp.json", {"mcpServers": {
        "piia-engram": {"command": "piia-engram-mcp", "args": [],
                        "env": {"ENGRAM_DIR": "/data/engram", "OWNER_KEY": _SECRET}},
        "other": {"command": "x"}}})
    W._write_mcp_config(path, sys.executable, "piia_engram.mcp_server", None)

    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert sorted(servers) == ["engram", "other"]
    assert servers["engram"]["env"]["ENGRAM_DIR"] == "/data/engram"
    assert servers["engram"]["env"]["OWNER_KEY"] == _SECRET
    assert list(path.parent.glob("mcp.json.engram-backup.*"))  # backed up first
    out = capsys.readouterr().out
    assert "piia-engram -> engram" in out and _SECRET not in out


def test_setup_leaves_a_differently_named_engram_server_and_says_so(home, tmp_path, capsys):
    path = _write(home / ".cursor" / "mcp.json", {"mcpServers": {"memory": ENTRIES["memory"]}})
    W._write_mcp_config(path, sys.executable, "piia_engram.mcp_server", None)
    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert servers["memory"] == ENTRIES["memory"] and "engram" in servers
    assert "memory" in capsys.readouterr().out


def test_setup_keeps_a_piia_engram_key_that_is_not_engram(home, tmp_path):
    path = _write(home / ".cursor" / "mcp.json", {"mcpServers": {"piia-engram": {"command": "npx", "args": ["x"]}}})
    W._write_mcp_config(path, sys.executable, "piia_engram.mcp_server", None)
    servers = json.loads(path.read_text(encoding="utf-8"))["mcpServers"]
    assert servers["piia-engram"] == {"command": "npx", "args": ["x"]} and "engram" in servers


def test_codex_toml_migrates_piia_engram(home, tmp_path):
    path = _write(home / ".codex" / "config.toml", (
        '[mcp_servers.other]\ncommand = "x"\n\n'
        '[mcp_servers.piia-engram]\ncommand = "piia-engram-mcp"\nargs = []\n\n'
        '[mcp_servers.piia-engram.env]\nENGRAM_DIR = "/data/engram"\n'))
    W._write_mcp_config_toml(path, sys.executable, "piia_engram.mcp_server", None)
    text = path.read_text(encoding="utf-8")
    assert "piia-engram]" not in text and "[mcp_servers.engram]" in text
    assert "[mcp_servers.other]" in text
    assert 'ENGRAM_DIR = "/data/engram"' in text
    parsed = W._parse_toml(text)
    assert sorted(parsed["mcp_servers"]) == ["engram", "other"]
