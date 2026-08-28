"""Tests for scripts/count_mcp_tools.py.

The helper is the source command behind the manifest's tool-split facts, so it
must agree with the real source and with the rest of the packaging test suite.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "count_mcp_tools.py"


def _load():
    spec = importlib.util.spec_from_file_location("_count_mcp_tools", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def counter():
    return _load()


def test_derive_matches_known_split(counter):
    """The committed split is 59 total = 19 core + 40 advanced."""
    counts = counter.derive(ROOT)
    assert counts["total"] == 59
    assert counts["core"] == 19
    assert counts["advanced"] == 40
    assert counts["core"] + counts["advanced"] == counts["total"]


def test_total_matches_independent_ast_count(counter):
    """Cross-check total against an independent AST walk (no shared helper)."""
    pkg = ROOT / "src" / "piia_engram"
    files = [pkg / "mcp_server.py", *sorted(pkg.glob("mcp_tools_*.py"))]
    independent = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        independent += sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef)
            and any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "tool"
                for d in n.decorator_list
            )
        )
    assert counter.derive(ROOT)["total"] == independent


def test_core_matches_packaging_core_set(counter):
    """Core count must equal the size of the packaging suite's CORE_MCP_TOOLS."""
    from tests.test_packaging import CORE_MCP_TOOLS

    assert counter.derive(ROOT)["core"] == len(CORE_MCP_TOOLS)


def test_json_output(counter, tmp_path, monkeypatch, capsys):
    """--json emits a parseable object with total/core/advanced."""
    import json
    import sys

    monkeypatch.setattr(sys, "argv", [
        "count_mcp_tools.py", "--root", str(ROOT), "--json",
    ])
    assert counter.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"total": 59, "core": 19, "advanced": 40}
