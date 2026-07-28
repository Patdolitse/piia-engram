from __future__ import annotations

import json
from pathlib import Path

from piia_engram import check_runtime_compatibility, get_runtime_capabilities
from piia_engram import runtime_capabilities as capabilities


ROOT = Path(__file__).resolve().parent.parent


def test_runtime_capability_fingerprint_is_deterministic_and_content_free():
    first = get_runtime_capabilities()
    second = get_runtime_capabilities()

    assert first == second
    assert first["schema"] == "engram_runtime_capabilities.v1"
    assert first["fingerprint"].startswith("sha256:")
    blob = json.dumps(first, ensure_ascii=False)
    assert str(ROOT) not in blob
    assert "project_folder" not in blob
    assert "metadata_only_no_user_content" in blob


def test_fingerprint_covers_contracts_not_runtime_version(monkeypatch):
    first = get_runtime_capabilities()
    monkeypatch.setattr(capabilities, "_runtime_version", lambda: "99.0.0")
    second = get_runtime_capabilities()

    assert second["runtime_version"] == "99.0.0"
    assert second["fingerprint"] == first["fingerprint"]


def test_runtime_version_prefers_the_executing_source_module(monkeypatch):
    from piia_engram import __version__

    monkeypatch.setattr(capabilities, "version", lambda _name: "0.0.0")

    assert capabilities._runtime_version() == __version__


def test_runtime_compatibility_accepts_capability_codes_and_contracts():
    result = check_runtime_compatibility(
        required_codes=[
            "exact_project_scope",
            "project_scoped_reconcile",
            "wrap_up_status_by_idempotency_key",
        ],
        required_contracts={
            "project_snapshot": "project_snapshot.v2",
            "read_path": "zero_write_read_path.v1",
        },
    )

    assert result["compatible"] is True
    assert result["missing_codes"] == []
    assert result["contract_mismatches"] == {}


def test_runtime_compatibility_explains_missing_requirements():
    result = check_runtime_compatibility(
        required_codes=["nonexistent_capability"],
        required_contracts={"project_snapshot": "project_snapshot.v999"},
    )

    assert result["compatible"] is False
    assert result["missing_codes"] == ["nonexistent_capability"]
    assert result["contract_mismatches"]["project_snapshot"] == {
        "required": "project_snapshot.v999",
        "actual": "project_snapshot.v2",
    }


def test_capability_surface_counts_match_mcp_source():
    from piia_engram import mcp_server

    assert capabilities.MCP_SURFACE == {
        "total": len(mcp_server.ALL_CAPABILITY_TOOLS),
        "core": len(mcp_server.TIER1_TOOLS),
        "advanced": (
            len(mcp_server.ALL_CAPABILITY_TOOLS) - len(mcp_server.TIER1_TOOLS)
        ),
    }


def test_capabilities_cli_json_and_requirement_exit_codes(capsys):
    from piia_engram.setup_wizard import _run_capabilities_cli

    assert _run_capabilities_cli([
        "--json",
        "--require",
        "exact_project_scope",
        "--contract",
        "project_snapshot=project_snapshot.v2",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatibility"]["compatible"] is True

    assert _run_capabilities_cli([
        "--json",
        "--require",
        "missing_capability",
    ]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["compatibility"]["compatible"] is False
