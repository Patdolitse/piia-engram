"""Focused contract tests for the knowledge search application service."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram import knowledge_search_service as svc


class _FakeEngram:
    def __init__(self, result=None, exc: Exception | None = None):
        self.result = result
        self.exc = exc
        self.calls = []

    def search_knowledge(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.result


def test_service_forwards_explicit_inputs_and_preserves_raw_result_identity():
    raw = {
        "lessons": [{"id": "L1"}],
        "decisions": [],
        "playbooks": [],
        "extra_metadata": {"cursor": "keep-me"},
    }
    eng = _FakeEngram(result=raw)

    out = svc.search_knowledge(
        eng,
        query="pytest fixture",
        scope="lessons",
        limit=3,
        filters={"tier": "verified"},
        project_folder="/project",
        allow_hybrid_index=False,
    )

    assert out is raw
    assert eng.calls == [
        (
            ("pytest fixture",),
            {
                "scope": "lessons",
                "limit": 3,
                "filters": {"tier": "verified"},
                "allow_hybrid_index": False,
                "project_folder": "/project",
            },
        )
    ]


def test_service_propagates_core_exceptions_verbatim():
    boom = RuntimeError("core search boom")
    eng = _FakeEngram(exc=boom)

    with pytest.raises(RuntimeError) as raised:
        svc.search_knowledge(eng, query="x")

    assert raised.value is boom
    assert eng.calls


def test_service_has_no_transport_or_runtime_imports():
    path = Path(svc.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_modules = {
        "argparse",
        "mcp",
        "mcp_server",
        "piia_engram.mcp_server",
        "piia_engram.cli_commands",
        "piia_engram.governance_runtime",
        "piia_engram.telemetry",
    }
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported.isdisjoint(forbidden_modules)
    assert all("FastMCP" not in name for name in imported)


def test_service_plain_script_import_smoke():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import knowledge_search_service as svc; "
                "print('PLAIN_SERVICE_OK', callable(svc.search_knowledge))"
            ),
        ],
        cwd=str(Path(svc.__file__).parent),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert "PLAIN_SERVICE_OK True" in completed.stdout
