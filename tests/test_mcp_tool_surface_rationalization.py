"""Regression tests for MCP tool-surface classification and schema wording."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = ROOT / "src" / "piia_engram" / "mcp_server.py"


def _tree() -> ast.Module:
    return ast.parse(MCP_SERVER.read_text(encoding="utf-8"))


def _literal_assignment(name: str):
    for node in ast.walk(_tree()):
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
    for node in ast.walk(_tree()):
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

    assert len(tier1) == 17
    assert classes["get_identity_card"] == "export_owner_only"
    assert "get_identity_card" in tier1
    assert "Owner/export surface" in docs["get_identity_card"]

    assert classes["get_work_style"] == "read"
    assert "Deprecated compatibility read" in docs["get_work_style"]

    assert classes["classify_legacy_playbooks"] == "read"
    assert classes["get_playbook_scope_review_queue"] == "read"
    assert classes["apply_legacy_playbook_scope_suggestions"] == "owner_only_write"
    assert classes["rollback_playbook_scope_migration"] == "owner_only_write"
    assert classes["resolve_playbook_scope_review"] == "owner_only_write"

    assert classes["register_tool"] == "governed_write"
    assert classes["find_tool"] == "read"
    assert classes["list_tools"] == "read"
    assert classes["read_web_content"] == "read"
