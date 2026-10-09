"""Guard the published MCP tool-surface taxonomy against source drift."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COUNT_SCRIPT = ROOT / "scripts" / "count_mcp_tools.py"
TAXONOMY = ROOT / "docs" / "mcp-tool-surface.json"


def _load_counter():
    spec = importlib.util.spec_from_file_location("_count_mcp_tools", COUNT_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mcp_tool_surface_counts_match_source():
    counter = _load_counter()
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))

    assert taxonomy["counts"] == counter.derive(ROOT)


def test_mcp_tool_surface_keeps_preview_and_export_boundaries():
    taxonomy = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    boundaries = taxonomy["notable_boundaries"]

    assert "owner-gated export" in boundaries["get_identity_card"]
    assert "proposal-only" in boundaries["preview_context_governance"]
    assert "not stable" in boundaries["preview_context_governance"]


def test_current_docs_use_canonical_core_and_total_counts():
    import re
    counts = _load_counter().derive(ROOT)
    paths = [ROOT / "README.md", ROOT / "README.zh-CN.md", * (ROOT / "docs").rglob("*.md")]
    for path in paths:
        if "internal" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?<![-\w])(\d+) (?:core (?:MCP )?tools|个核心(?: MCP)? 工具|个核心工具)", text, re.I):
            assert int(match.group(1)) == counts["core"], (path.name, match.group(0))
        for match in re.finditer(r"(?:ships|all) (\d+) (?:MCP )?tools", text, re.I):
            assert int(match.group(1)) == counts["total"], (path.name, match.group(0))
