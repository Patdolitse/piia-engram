"""Regression tests for MCP tool-surface classification and schema wording."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = ROOT / "src" / "piia_engram" / "mcp_server.py"
TOOL_SURFACE = MCP_SERVER.parent / "tool_surface.py"


def _trees() -> list[ast.Module]:
    files = [
        MCP_SERVER,
        TOOL_SURFACE,
        *sorted(MCP_SERVER.parent.glob("mcp_tools_*.py")),
    ]
    return [ast.parse(f.read_text(encoding="utf-8")) for f in files]


def _walk_all():
    for tree in _trees():
        yield from ast.walk(tree)


def _literal_assignment(name: str):
    for node in _walk_all():
        value = None
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value = node.value
        if value is None:
            continue
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
            and value.args
        ):
            return frozenset(ast.literal_eval(value.args[0]))
        return ast.literal_eval(value)
    raise AssertionError(f"{name} assignment not found")


def _tool_docstrings() -> dict[str, str]:
    docs: dict[str, str] = {}
    for node in _walk_all():
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
            for dec in node.decorator_list
        ):
            continue
        docs[node.name] = ast.get_docstring(node) or ""
    return docs


def test_owner_export_and_admin_tools_expose_schema_markers():
    classes = _literal_assignment("TOOL_GOVERNANCE_CLASS")
    docs = _tool_docstrings()

    for tool, tool_class in classes.items():
        if tool_class == "export_owner_only":
            assert "Owner/export surface" in docs[tool], tool
        elif tool_class == "owner_only_write":
            assert "Owner/admin surface" in docs[tool], tool


def test_tool_surface_classification_pins_local_legacy_and_core_export_tools():
    classes = _literal_assignment("TOOL_GOVERNANCE_CLASS")
    tier1 = _literal_assignment("TIER1_TOOLS")
    docs = _tool_docstrings()

    assert len(tier1) == 18
    assert classes["get_identity_card"] == "export_owner_only"
    assert "get_identity_card" in tier1
    assert "Owner/export surface" in docs["get_identity_card"]

    # v4.0 merged tools: readers stay "read", mutating hubs are governed,
    # trust admin stays owner-only.
    assert classes["get_identity_facets"] == "read"
    assert classes["get_playbooks"] == "read"
    assert classes["explore_knowledge"] == "read"
    assert classes["manage_playbook"] == "governed_write"
    assert classes["playbook_execution"] == "governed_write"
    assert classes["review_staging"] == "governed_write"
    assert classes["manage_relation"] == "governed_write"
    assert classes["user_portrait"] == "governed_write"
    assert classes["manage_caller_trust"] == "owner_only_write"
    assert classes["onboard_repo"] == "owner_only_write"
    assert classes["onboard_accept"] == "owner_only_write"

    # Legacy playbook scope migration left the MCP surface (owner CLI now);
    # the governance table must not keep dangling entries.
    for legacy in (
        "get_work_style",
        "classify_legacy_playbooks",
        "get_playbook_scope_review_queue",
        "apply_legacy_playbook_scope_suggestions",
        "rollback_playbook_scope_migration",
        "resolve_playbook_scope_review",
    ):
        assert legacy not in classes, f"legacy entry {legacy} still classified"

    assert classes["register_tool"] == "governed_write"
    assert classes["find_tool"] == "read"
    assert classes["list_tools"] == "read"
    assert classes["read_web_content"] == "read"
