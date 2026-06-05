"""Tests for scripts/check_public_fact_sync.py and docs/public-facts.json.

The public-fact guard is a deterministic anti-drift enforcement: current-state
public docs must agree with the machine-readable fact manifest, while historical
surfaces (CHANGELOG, release-evidence/) are intentionally left alone.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "check_public_fact_sync.py"
_MANIFEST = ROOT / "docs" / "public-facts.json"


def _load():
    spec = importlib.util.spec_from_file_location("_public_fact_sync", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load()


# ---------------------------------------------------------------------------
# Real repo / manifest
# ---------------------------------------------------------------------------

def test_guard_passes_on_current_repo(guard):
    """The committed docs must already be in sync with the committed manifest."""
    ok, report = guard.run(_MANIFEST, ROOT)
    assert ok is True, report["problems"]


def test_manifest_schema_is_complete_and_consistent(guard):
    """Every required key is present and the internal invariants hold."""
    manifest = guard.load_manifest(_MANIFEST)
    problems = guard.validate_manifest_schema(manifest)
    assert problems == [], problems
    facts = manifest["facts"]
    assert facts["test_passed"] + facts["test_skipped"] == facts["test_collected"]
    assert (
        facts["mcp_tools_core"] + facts["mcp_tools_advanced"]
        == facts["mcp_tools_total"]
    )


def test_manifest_version_tracks_pyproject(guard):
    """Manifest must not lag the package version source of truth."""
    manifest = guard.load_manifest(_MANIFEST)
    assert manifest["local_dev_version"] == guard._pyproject_version(ROOT)


# ---------------------------------------------------------------------------
# Synthetic roots - exercise each drift mode in isolation
# ---------------------------------------------------------------------------

def _base_manifest() -> dict:
    return {
        "schema_version": 1,
        "package_name": "piia-engram",
        "local_dev_version": "3.47.0",
        "release_frame": "dev truth, not a publish",
        "facts": {
            "test_passed": 2398,
            "test_skipped": 8,
            "test_collected": 2406,
            "mcp_tools_total": 80,
            "mcp_tools_core": 16,
            "mcp_tools_advanced": 64,
            "telemetry_default": "off",
            "telemetry_remote_default": "off",
        },
        "last_verified_date": "2026-06-03",
        "sources": {"test_passed": "pytest"},
        "current_state_surfaces": ["README.md"],
        "historical_surfaces": ["CHANGELOG.md", "release-evidence/"],
        "checks": {},
    }


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _write_manifest(root: Path, manifest: dict) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    path = root / "docs" / "public-facts.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    # Keep the pyproject cross-check happy unless a test overrides it.
    if not (root / "pyproject.toml").is_file():
        _write(root, "pyproject.toml",
               f'[project]\nversion = "{manifest["local_dev_version"]}"\n')
    return path


def test_stale_test_count_fails(guard, tmp_path):
    """A stale '2346' rendering in a current-state README must fail."""
    m = _base_manifest()
    m["checks"] = {
        "test_count_patterns": [
            {"file": "README.md", "pattern": r"Tests passing \| \*\*(\d+)\*\*"}
        ]
    }
    path = _write_manifest(tmp_path, m)
    _write(tmp_path, "README.md", "| Tests passing | **2346** (8 skipped) |\n")
    ok, report = guard.run(path, tmp_path)
    assert ok is False
    assert any("2346" in p and "stale test count" in p for p in report["problems"])


def test_current_test_count_passes(guard, tmp_path):
    """The matching current number must pass the same check."""
    m = _base_manifest()
    m["checks"] = {
        "test_count_patterns": [
            {"file": "README.md", "pattern": r"Tests passing \| \*\*(\d+)\*\*"}
        ]
    }
    path = _write_manifest(tmp_path, m)
    _write(tmp_path, "README.md", "| Tests passing | **2398** (8 skipped) |\n")
    ok, report = guard.run(path, tmp_path)
    assert ok is True, report["problems"]


def test_stale_tool_count_fails(guard, tmp_path):
    """A required current-state substring (tool split) gone missing must fail."""
    m = _base_manifest()
    m["checks"] = {
        "required_substrings": [
            {"file": "README.md", "must_contain": ["**65 Advanced**"]}
        ]
    }
    path = _write_manifest(tmp_path, m)
    # Stale tool count: 60 instead of 65.
    _write(tmp_path, "README.md", "MCP tools: **16 Core** + **60 Advanced**\n")
    ok, report = guard.run(path, tmp_path)
    assert ok is False
    assert any("65 Advanced" in p for p in report["problems"])


def test_stale_version_fails(guard, tmp_path):
    """A version-bearing surface carrying an old version must fail."""
    m = _base_manifest()
    m["current_state_surfaces"] = [".mcp/server.json"]
    m["checks"] = {
        "version_bearing": [
            {"file": ".mcp/server.json", "pattern": r'"version":\s*"([0-9][^"]*)"'}
        ]
    }
    path = _write_manifest(tmp_path, m)
    _write(tmp_path, ".mcp/server.json", '{\n  "version": "3.28.1"\n}\n')
    ok, report = guard.run(path, tmp_path)
    assert ok is False
    assert any("3.28.1" in p and "3.47.0" in p for p in report["problems"])


def test_historical_evidence_is_not_policed(guard, tmp_path):
    """Old numbers in CHANGELOG / release-evidence must NOT fail the guard."""
    m = _base_manifest()
    m["current_state_surfaces"] = ["README.md"]
    m["checks"] = {"forbidden_in_current_state": ["**2346**"]}
    path = _write_manifest(tmp_path, m)
    _write(tmp_path, "README.md", "| Tests passing | **2398** |\n")  # clean current
    _write(tmp_path, "CHANGELOG.md", "## [3.46.0]\n- tests: **2346** passed\n")
    _write(tmp_path, "release-evidence/v3.46.0.md", "- tests: 2346 passed\n")
    ok, report = guard.run(path, tmp_path)
    assert ok is True, report["problems"]


def test_known_stale_string_in_current_surface_fails(guard, tmp_path):
    """The literal known-stale marker in a current-state surface must fail."""
    m = _base_manifest()
    m["current_state_surfaces"] = ["README.md"]
    m["checks"] = {"forbidden_in_current_state": ["**2346**"]}
    path = _write_manifest(tmp_path, m)
    _write(tmp_path, "README.md", "| Tests passing | **2346** |\n")
    ok, report = guard.run(path, tmp_path)
    assert ok is False
    assert any("known-stale" in p for p in report["problems"])


def test_inconsistent_manifest_is_setup_error(guard, tmp_path):
    """A manifest whose own numbers contradict each other is a setup error."""
    m = _base_manifest()
    m["facts"]["test_collected"] = 9999  # breaks passed+skipped==collected
    path = _write_manifest(tmp_path, m)
    with pytest.raises(guard.SetupError):
        guard.run(path, tmp_path)


def test_missing_surface_is_setup_error(guard, tmp_path):
    """A policed surface that cannot be read fails closed (setup error)."""
    m = _base_manifest()
    m["current_state_surfaces"] = ["README.md"]
    m["checks"] = {"forbidden_in_current_state": ["**2346**"]}
    path = _write_manifest(tmp_path, m)
    # README.md intentionally not written.
    with pytest.raises(guard.SetupError):
        guard.run(path, tmp_path)


def test_json_output_is_machine_readable(guard, tmp_path, monkeypatch, capsys):
    """--json emits parseable JSON with an 'ok' verdict."""
    import sys

    m = _base_manifest()
    m["checks"] = {
        "test_count_patterns": [
            {"file": "README.md", "pattern": r"Tests passing \| \*\*(\d+)\*\*"}
        ]
    }
    _write_manifest(tmp_path, m)
    _write(tmp_path, "README.md", "| Tests passing | **2398** |\n")

    monkeypatch.setattr(sys, "argv", [
        "check_public_fact_sync.py",
        "--root", str(tmp_path),
        "--json",
    ])
    rc = guard.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert rc == 0
