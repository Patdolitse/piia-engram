"""Regression tests for public positioning, listing copy, and demos."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mcp_registry_description_stays_within_limit():
    server = json.loads(_read(".mcp/server.json"))

    assert len(server["description"]) <= 100
    assert "every AI tool" not in server["description"].lower()
    assert "User-approved" in server["description"]


def test_pyproject_description_uses_v341_positioning():
    data = tomllib.loads(_read("pyproject.toml"))
    desc = data["project"]["description"]

    assert "Local-first personal AI identity and memory" in desc
    assert "MCP-compatible coding tools" in desc
    assert "One memory" not in desc
    assert "every AI tool" not in desc.lower()


def test_public_package_metadata_uses_v341_positioning():
    pyproject = tomllib.loads(_read("pyproject.toml"))
    server = json.loads(_read(".mcp/server.json"))
    plugin = json.loads(_read(".claude-plugin/plugin.json"))

    assert pyproject["project"]["version"] == "3.41.0"
    assert server["version"] == "3.41.0"
    assert server["packages"][0]["version"] == "3.41.0"
    assert plugin["version"] == "3.41.0"
    assert "MCP-compatible coding tools" in plugin["description"]
    assert "every AI tool" not in plugin["description"].lower()


def test_public_positioning_docs_do_not_reintroduce_overclaims():
    files = [
        "README.md",
        "README.zh-CN.md",
        "docs/listing-copy.md",
        "docs/cross-tool-continuity-demo.md",
        "docs/trust.md",
        "docs/comparison.md",
    ]
    text = "\n".join(_read(path) for path in files)

    forbidden = [
        "One memory. Every AI tool",
        "every AI tool remembers",
        "full context",
        "only approval",
        "absolutely secure",
        "no network ever",
        "local-log-only",
        "所有 AI 都记住",
        "完整上下文",
        "唯一审批",
        "绝对安全",
    ]
    for phrase in forbidden:
        assert phrase not in text


def test_new_public_docs_are_publish_allowlisted():
    allowlist = _read(".publishallow")

    for path in [
        "docs/trust.md",
        "docs/listing-copy.md",
        "docs/cross-tool-continuity-demo.md",
    ]:
        assert path in allowlist


def test_cross_tool_demo_doc_uses_public_safe_paths():
    doc = _read("docs/cross-tool-continuity-demo.md")

    assert "E:\\codex-runtimes" not in doc
    assert "C:\\Users" not in doc
    assert "<demo-root>" in doc
    assert "~/.engram" in doc
    assert "source_tool=claude_code_demo" in doc


def test_cross_tool_continuity_demo_uses_isolated_store():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "demos/cross_tool_continuity_demo.py"],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "Store: <demo-root>" in out
    assert "not ~/.engram" in out
    assert "found: yes" in out
    assert "source_tool: claude_code_demo" in out
    assert str(Path.home()) not in out
    assert "DATA FRAGMENTATION" not in out
