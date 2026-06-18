"""Dock M2: owner-confidence governance readiness summary (dock-governance).

Zero-write, pathless: reports process governance state + per-client
ENGRAM_GOVERNANCE env coverage + a writes_config=false recommendation. The
client rows expose name / status / governance_env only — never config paths,
command args, raw config, or exception text (Codex boundary).
"""
from __future__ import annotations

import json

import pytest

from piia_engram import setup_wizard  # noqa: F401 — resolve setup_wizard<->cli_commands cycle
from piia_engram import cli_commands
from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path, monkeypatch) -> Engram:
    root = tmp_path / "engram"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return Engram(root=root)


def test_dock_governance_json_shape_zero_write(eng, capsys, tmp_path):
    store = tmp_path / "engram"
    before = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}

    rc = cli_commands._run_dock_governance(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)

    assert payload["ok"] is True
    assert payload["read_only"] is True
    assert payload["dock_contract_version"] == "M1"
    assert "enabled" in payload["governance"]
    cg = payload["client_governance"]
    for k in ("configured", "governance_enabled", "needs_governance", "total"):
        assert k in cg
    assert payload["recommendation"]["writes_config"] is False

    after = {p.name: p.stat().st_mtime_ns for p in store.glob("*.json")}
    assert before == after  # zero-write: store untouched


def test_dock_governance_no_config_paths_or_exception_text(eng, capsys):
    rc = cli_commands._run_dock_governance(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    # process governance block carries no raw exception text
    assert "error" not in payload["governance"]
    # client rows are metadata-only: no config paths / args / raw config
    for client in payload["client_governance"].get("clients", []):
        assert "config_path" not in client
        assert "config_paths" not in client
        assert "path" not in client
        assert "args" not in client


def test_dock_governance_wired_into_cli_dispatch():
    import inspect
    from piia_engram import setup_wizard as W

    src = inspect.getsource(W.main)
    assert '"dock-governance"' in src or "'dock-governance'" in src
