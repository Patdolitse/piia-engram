"""Tests for marketplace-facing MCP metadata."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_marketplace_metadata.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_marketplace_metadata", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_marketplace_metadata_guard_passes_current_repo():
    mod = _load()
    report = mod.check(ROOT)

    assert report["ok"] is True, report["problems"]
    assert report["displayName"] == "Piia Engram"
    assert report["slug"] == "patdolitse-piia-engram"
    assert report["facts"]["mcp_tools_total"] == 58


def test_marketplace_metadata_guard_catches_fallback_title(tmp_path):
    mod = _load()
    _copy_minimal_repo(tmp_path)

    server_path = tmp_path / ".mcp" / "server.json"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    server["title"] = "MCP Server Manifest Plugin"
    server_path.write_text(json.dumps(server), encoding="utf-8")

    report = mod.check(tmp_path)
    assert report["ok"] is False
    assert any("title must be 'Piia Engram'" in p for p in report["problems"])
    assert any("fallback title" in p for p in report["problems"])


def test_marketplace_metadata_guard_requires_mcp_console_script(tmp_path):
    mod = _load()
    _copy_minimal_repo(tmp_path)

    server_path = tmp_path / ".mcp" / "server.json"
    server = json.loads(server_path.read_text(encoding="utf-8"))
    package = server["packages"][0]
    package["runtimeArguments"] = [
        {"type": "named", "name": "--from", "value": "piia-engram==4.2.0"}
    ]
    server_path.write_text(json.dumps(server), encoding="utf-8")

    report = mod.check(tmp_path)

    assert report["ok"] is False
    assert any("runtimeArguments must run piia-engram-mcp" in p for p in report["problems"])


def _copy_minimal_repo(root: Path) -> None:
    files = [
        "pyproject.toml",
        "docs/public-facts.json",
        ".mcp/server.json",
        "README.md",
        "README.zh-CN.md",
        "glama.yaml",
        "skills/engram/SKILL.md",
    ]
    for rel in files:
        src = ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
