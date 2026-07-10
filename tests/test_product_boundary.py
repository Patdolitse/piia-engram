"""Tests for the canonical public product-boundary guard."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_product_boundary.py"


def _load():
    spec = importlib.util.spec_from_file_location("_product_boundary", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def guard():
    return _load()


def test_product_boundary_guard_passes_current_repo(guard):
    ok, report = guard.run(ROOT)

    assert ok is True, report["problems"]
    assert report["source_doc"] == "docs/product-boundary.md"
    assert report["audit"]["tracked_files"] > 0
    assert "package_source" in report["audit"]["categories"]


def test_product_boundary_contract_is_in_public_facts():
    data = json.loads((ROOT / "docs" / "public-facts.json").read_text(encoding="utf-8"))
    contract = data["product_boundary_contract"]

    assert contract["status"] == "canonical_public_product_boundary"
    assert "identity" in contract["public_core"]
    assert "reader" in contract["public_advanced_adapters"]
    assert contract["optional_extensions"]["reader"]
    assert "docs/product-boundary.md" in contract["public_surface_files"]
    assert "docs/tool-surface-analysis.md" in contract["public_surface_files"]
    assert "glama.yaml" in contract["public_surface_files"]


def test_synthetic_private_module_and_import_are_blocked_without_raw_echo(guard, tmp_path):
    contract = guard.load_contract(ROOT)
    raw_module = "src/piia_engram/private_research_payload.py"
    raw_import = "from piia_engram.private_research_payload import secret\n"

    problems = guard.check_package_surface(
        tmp_path,
        contract,
        tracked_files=[raw_module, "src/piia_engram/public_adapter.py"],
        text_by_rel={
            raw_module: "VALUE = 1\n",
            "src/piia_engram/public_adapter.py": raw_import,
        },
    )
    rendered = json.dumps(problems, ensure_ascii=False)

    assert {p["code"] for p in problems} == {
        "package_module_private_marker",
        "package_import_private_marker",
    }
    assert "private_research_payload" not in rendered
    assert raw_import not in rendered


def test_synthetic_public_surface_failure_does_not_echo_unsafe_text(guard):
    contract = guard.load_contract(ROOT)
    raw_path = r"C:\Users\victim\secret\notes.txt"
    raw_term = "private research branch codename should never print"

    problems = guard.check_public_surfaces(
        ROOT,
        contract,
        text_by_rel={
            "README.md": f"oops {raw_path}\n",
            "docs/product-boundary.md": f"oops {raw_term}\n",
        },
    )
    rendered = json.dumps(problems, ensure_ascii=False)

    assert any(p["code"] == "public_surface_private_path" for p in problems)
    assert any(p["code"] == "public_surface_private_term" for p in problems)
    assert raw_path not in rendered
    assert raw_term not in rendered
    assert "victim" not in rendered


def test_mcp_and_cli_surface_counts_remain_unchanged(guard):
    count_script = ROOT / "scripts" / "count_mcp_tools.py"
    spec = importlib.util.spec_from_file_location("_count_mcp_tools", count_script)
    assert spec and spec.loader
    counter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(counter)

    facts = json.loads((ROOT / "docs" / "public-facts.json").read_text(encoding="utf-8"))
    counts = counter.derive(ROOT)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert counts == {
        "total": facts["facts"]["mcp_tools_total"],
        "core": facts["facts"]["mcp_tools_core"],
        "advanced": facts["facts"]["mcp_tools_advanced"],
    }
    assert "check_product_boundary" not in pyproject
