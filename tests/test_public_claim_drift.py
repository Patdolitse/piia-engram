"""Tests for the public claim drift sweep.

The sweep is intentionally broader than the explicit public-facts sync guard:
it scans tracked/current Markdown surfaces for quantified self-claims and
absolute compatibility overclaims, while skipping historical surfaces.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_public_claim_drift.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("check_public_claim_drift", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(root: Path) -> None:
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "public-facts.json").write_text(
        json.dumps(
            {
                "facts": {
                    "test_passed": 2687,
                    "test_skipped": 8,
                    "test_collected": 2695,
                    "mcp_tools_total": 80,
                }
            }
        ),
        encoding="utf-8",
    )


def test_catches_stale_test_count_in_new_markdown_surface(mod, tmp_path: Path):
    _write_manifest(tmp_path)
    (tmp_path / "NEW_GUIDE.md").write_text("Engram has 2646 passed tests.\n", encoding="utf-8")

    result = mod.scan(tmp_path)

    assert result["ok"] is False
    assert result["problems"] == [
        {
            "file": "NEW_GUIDE.md",
            "kind": "stale_claim",
            "fact": "test_passed",
            "claimed": 2646,
            "expected": 2687,
        }
    ]


def test_accepts_current_counts_and_tool_count(mod, tmp_path: Path):
    _write_manifest(tmp_path)
    (tmp_path / "README.md").write_text(
        "2687 passed, 8 skipped, 2695 tests collected. Ships 80 MCP tools.\n",
        encoding="utf-8",
    )

    result = mod.scan(tmp_path)

    assert result["ok"] is True
    assert result["problems"] == []


def test_catches_stale_chinese_tool_count(mod, tmp_path: Path):
    _write_manifest(tmp_path)
    (tmp_path / "README.zh-CN.md").write_text(
        "piia-engram 提供 81 个知识生命周期管理工具。\n",
        encoding="utf-8",
    )

    result = mod.scan(tmp_path)

    assert result["ok"] is False
    assert result["problems"] == [
        {
            "file": "README.zh-CN.md",
            "kind": "stale_claim",
            "fact": "mcp_tools_total",
            "claimed": 81,
            "expected": 80,
        }
    ]


def test_catches_public_overclaim_phrase(mod, tmp_path: Path):
    _write_manifest(tmp_path)
    (tmp_path / "copy.md").write_text(
        "Engram works with every AI tool and gives guaranteed continuity.\n",
        encoding="utf-8",
    )

    result = mod.scan(tmp_path)

    assert result["ok"] is False
    kinds = {(p["kind"], p.get("phrase")) for p in result["problems"]}
    assert ("overclaim_phrase", "works with every ai tool") in kinds
    assert ("overclaim_phrase", "guaranteed continuity") in kinds


def test_negated_bad_examples_do_not_trigger_overclaim(mod, tmp_path: Path):
    _write_manifest(tmp_path)
    (tmp_path / "positioning.md").write_text(
        "Avoid broad claim language.\n"
        "Do not say: Engram works with every AI tool.\n"
        "Instead say: tested with specific local clients.\n",
        encoding="utf-8",
    )

    result = mod.scan(tmp_path)

    assert result["ok"] is True
    assert result["problems"] == []


def test_historical_surfaces_are_skipped(mod, tmp_path: Path):
    _write_manifest(tmp_path)
    (tmp_path / "release-evidence").mkdir()
    (tmp_path / "release-evidence" / "v-old.md").write_text(
        "At the time, 2346 passed.\n",
        encoding="utf-8",
    )

    result = mod.scan(tmp_path)

    assert result["ok"] is True
    assert result["skipped"] == [
        {"file": "release-evidence/v-old.md", "reason": "historical"}
    ]
