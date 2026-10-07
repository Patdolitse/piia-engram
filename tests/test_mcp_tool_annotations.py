"""MCP tool annotations: the table, its snapshot and what clients actually see.

The hints are advice for the client, not access control. These tests pin the
whole table, keep it in step with the registered tools, and check that the
hints reach the tool definitions the server announces.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram.tool_annotations import TOOL_ANNOTATIONS, ToolHints, apply_tool_annotations

_ROOT = Path(__file__).resolve().parents[1]
_SNAPSHOT = _ROOT / "tests" / "snapshots" / "mcp_tool_annotations.json"


def _source_tool_names() -> set[str]:
    names: set[str] = set()
    for path in sorted((_ROOT / "src" / "piia_engram").glob("mcp_tools_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "tool":
                    names.add(node.name)
    return names


def _governance_class() -> dict[str, str]:
    import piia_engram.mcp_server as m

    return dict(m.TOOL_GOVERNANCE_CLASS)


def test_every_registered_tool_has_a_row_and_no_row_is_orphaned():
    source = _source_tool_names()
    assert len(source) >= 59
    assert source - set(TOOL_ANNOTATIONS) == set(), "tools without annotations"
    assert set(TOOL_ANNOTATIONS) - source == set(), "rows for tools that do not exist"


def test_table_matches_the_committed_snapshot():
    expected = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    actual = {name: hints.as_dict() for name, hints in sorted(TOOL_ANNOTATIONS.items())}
    assert actual == expected, "update tests/snapshots/mcp_tool_annotations.json on purpose"


def test_read_web_content_is_the_only_open_world_tool():
    assert {n for n, h in TOOL_ANNOTATIONS.items() if h.open_world} == {"read_web_content"}


# Owner-gated tools that only read over MCP: import_engram previews; applying an
# import is the local `engram import`; onboard_accept is the local `engram onboard-accept`.
OWNER_GATED_READS = frozenset({"import_engram", "onboard_accept"})


def test_classes_and_hints_agree():
    classes = _governance_class()
    assert set(classes) == set(TOOL_ANNOTATIONS)
    for name, hints in TOOL_ANNOTATIONS.items():
        cls = classes[name]
        if cls == "read":
            # access counters and usage logs are bookkeeping, not writes
            assert hints.read_only and hints.idempotent and not hints.destructive, name
        elif name in OWNER_GATED_READS:
            # owner-gated (and refused under strict) but writes nothing over MCP
            assert hints.read_only and hints.idempotent and not hints.destructive, name
        else:
            assert not hints.read_only, f"{name} ({cls}) writes, so it is not read-only"
    # a read-only tool never claims to be destructive
    assert not [n for n, h in TOOL_ANNOTATIONS.items() if h.read_only and h.destructive]


def test_destructive_tools_are_exactly_the_ones_that_overwrite_downgrade_or_retire():
    assert {n for n, h in TOOL_ANNOTATIONS.items() if h.destructive} == {
        "manage_caller_trust", "archive_knowledge",
        "merge_knowledge", "manage_relation", "update_identity", "user_portrait",
        "manage_playbook", "update_knowledge", "export_engram",
        "register_tool", "save_project_snapshot", "start_project", "wrap_up_session",
        "check_anchors",
    }
    # tools that only add stay unmarked: an automatic supersede adds a relation
    # and the old row stays readable by id
    for name in ("add_lesson", "add_decision", "add_playbook", "memory_store",
                 "ingest_notes", "extract_session_insights", "save_agent_context"):
        assert not TOOL_ANNOTATIONS[name].destructive, name
    # review_staging only lists, previews and refreshes last_reviewed over MCP:
    # approving, rejecting and archiving pending proposals is the local review
    assert not TOOL_ANNOTATIONS["review_staging"].destructive


def test_export_engram_overwrites_an_existing_file_so_it_is_marked_destructive(tmp_path, monkeypatch):
    """The reason for export_engram's destructiveHint: output_path is replaced silently."""
    import asyncio

    import piia_engram.mcp_server as m
    from piia_engram.core import Engram

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    monkeypatch.setattr(m, "_engram", Engram(tmp_path / "store"))
    target = tmp_path / "existing.json"
    target.write_text("PRECIOUS", encoding="utf-8")
    result = asyncio.run(m.export_engram(output_path=str(target)))
    assert "PRECIOUS" not in target.read_text(encoding="utf-8"), result
    assert TOOL_ANNOTATIONS["export_engram"].destructive


class _Tool:
    annotations = None


class _Manager:
    def __init__(self, names):
        self._tools = {n: _Tool() for n in names}


class _Server:
    def __init__(self, names):
        self._tool_manager = _Manager(names)


def test_apply_sets_annotations_and_skips_unknown_tools():
    server = _Server(["search_knowledge", "read_web_content", "not_a_tool"])
    assert apply_tool_annotations(server) == 2
    tools = server._tool_manager._tools
    assert tools["search_knowledge"].annotations.readOnlyHint is True
    assert tools["read_web_content"].annotations.openWorldHint is True
    assert tools["not_a_tool"].annotations is None


def test_apply_is_silent_when_the_mcp_package_has_no_annotations(monkeypatch):
    import mcp.types as mcp_types

    monkeypatch.delattr(mcp_types, "ToolAnnotations")
    assert apply_tool_annotations(_Server(["search_knowledge"])) == 0


def test_apply_warns_in_the_log_when_there_is_no_tool_table(caplog, capsys):
    import logging

    with caplog.at_level(logging.WARNING, logger="piia_engram.tool_annotations"):
        assert apply_tool_annotations(object()) == 0
    assert any("not applied" in r.getMessage() for r in caplog.records)
    out = capsys.readouterr()
    assert out.out == ""  # never on stdout: it carries the stdio protocol


def test_apply_is_silent_when_the_tool_model_has_no_annotations_field():
    class Model:
        model_fields = {"name": None}

    server = _Server(["search_knowledge"])
    server._tool_manager._tools["search_knowledge"] = Model()
    assert apply_tool_annotations(server) == 0
    assert apply_tool_annotations(object()) == 0


def test_announced_tool_definitions_carry_the_hints():
    import asyncio

    import piia_engram.mcp_server as m

    tools = asyncio.run(m.mcp.list_tools())
    assert tools, "no tools registered"
    for tool in tools:
        wire = tool.model_dump(by_alias=True, exclude_none=True)
        assert wire["annotations"] == TOOL_ANNOTATIONS[tool.name].as_dict(), tool.name


def test_every_tool_announces_its_hints_with_all_tools_enabled(tmp_path):
    code = (
        "import asyncio, json, piia_engram.mcp_server as m\n"
        "tools = asyncio.run(m.mcp.list_tools())\n"
        "print('ANNOTATIONS=' + json.dumps({t.name: t.model_dump(by_alias=True, exclude_none=True)"
        ".get('annotations') for t in tools}))\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env.update({
        "HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home / "AppData"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"), "DO_NOT_TRACK": "1",
        "ENGRAM_TOOLS": "all", "ENGRAM_DIR": str(tmp_path / "store"),
        "PYTHONPATH": str(_ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8", "ENGRAM_TEST": "1",
    })
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("ANNOTATIONS="))
    announced = json.loads(line[len("ANNOTATIONS="):])
    assert set(announced) == set(TOOL_ANNOTATIONS)
    for name, wire in announced.items():
        assert wire == TOOL_ANNOTATIONS[name].as_dict(), name
    # the one open-world tool, and only that one, says so on the wire
    assert [n for n, w in announced.items() if w["openWorldHint"]] == ["read_web_content"]


def test_annotations_are_applied_from_one_place_in_the_server_module():
    src = _ROOT / "src" / "piia_engram"
    callers = sorted(
        p.name for p in src.glob("*.py")
        if p.name != "tool_annotations.py" and "_apply_tool_annotations(" in p.read_text(encoding="utf-8")
    )
    assert callers == ["mcp_server.py"]
    text = (src / "mcp_server.py").read_text(encoding="utf-8")
    assert text.count("_apply_tool_annotations(mcp)") == 1
    # after every tool module is imported, directly before the tier filter
    assert text.index("from .mcp_tools_session import") < text.index("_apply_tool_annotations(mcp)")
    assert text.index("_apply_tool_annotations(mcp)\n_apply_tool_tier()") > 0
