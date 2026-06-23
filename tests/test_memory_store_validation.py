"""A1-3: memory_store must validate input types, not throw AttributeError.

Bug: Passing non-string kind or non-string content fields like
{"summary": 123} throws raw AttributeError instead of a clean message.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from piia_engram.core import Engram


class TestAddLessonFieldTypes:
    """Core layer must coerce non-string fields instead of crashing."""

    def test_int_summary_coerced(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.add_lesson({"summary": 123, "detail": "test"})
        assert isinstance(result, dict)
        assert "error" not in result

    def test_list_summary_coerced(self, tmp_path):
        eng = Engram(root=tmp_path)
        result = eng.add_lesson({"summary": ["a", "b"], "detail": "test"})
        assert isinstance(result, dict)
        assert "error" not in result


class TestMcpHandlerKindGuard:
    """MCP handler validates kind type before .strip()."""

    @staticmethod
    def _run_handler_script(kind_repr: str) -> dict:
        script = f'''
import sys, json, os
os.environ["ENGRAM_TEST"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, "src")
from piia_engram.mcp_tools_write import memory_store
import asyncio

async def run():
    result = await memory_store(
        kind={kind_repr},
        content_json='{{"summary": "test"}}',
    )
    print(json.dumps({{"ok": True, "result": str(result)}}))

try:
    asyncio.run(run())
except Exception as e:
    print(json.dumps({{"ok": False, "error": type(e).__name__, "msg": str(e)}}))
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, timeout=30,
            cwd=str(Path(__file__).resolve().parent.parent),
            env={
                **dict(__import__("os").environ),
                "PYTHONIOENCODING": "utf-8",
                "ENGRAM_TEST": "1",
                "PYTHONPATH": "src",
            },
        )
        stdout = result.stdout.decode("utf-8", errors="replace")
        for line in reversed(stdout.strip().split("\n")):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return {"ok": False, "error": "NoOutput", "msg": stdout}

    def test_none_kind_no_attribute_error(self):
        data = self._run_handler_script("None")
        if not data["ok"]:
            assert data["error"] != "AttributeError", \
                f"Got raw AttributeError: {data['msg']}"

    def test_int_kind_no_attribute_error(self):
        data = self._run_handler_script("123")
        if not data["ok"]:
            assert data["error"] != "AttributeError", \
                f"Got raw AttributeError: {data['msg']}"
