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


def test_pyproject_description_uses_current_positioning():
    data = tomllib.loads(_read("pyproject.toml"))
    desc = data["project"]["description"]

    assert "Local-first personal AI identity and memory" in desc
    assert "MCP-compatible coding tools" in desc
    assert "One memory" not in desc
    assert "every AI tool" not in desc.lower()


def test_public_package_metadata_uses_current_version():
    pyproject = tomllib.loads(_read("pyproject.toml"))
    server = json.loads(_read(".mcp/server.json"))
    plugin = json.loads(_read(".claude-plugin/plugin.json"))

    assert pyproject["project"]["version"] == "3.47.0"
    assert server["version"] == "3.47.0"
    assert server["packages"][0]["version"] == "3.47.0"
    assert plugin["version"] == "3.47.0"
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


def test_release_notes_bilingual_order_is_documented():
    listing = _read("docs/listing-copy.md")
    evidence = _read("release-evidence/README.md")

    assert "English first, then Chinese" in listing
    assert "## English" in listing
    assert "## Chinese" in listing
    assert evidence.index("## English") < evidence.index("## Chinese")
    assert "English must come first" in evidence


def test_setup_file_safety_docs_are_explicit_about_external_config_boundary():
    english = _read("README.md") + "\n" + _read("SECURITY.md") + "\n" + _read("docs/trust.md")
    chinese = _read("README.zh-CN.md")

    assert "external client configs stay read-only by default" in english
    assert "engram setup --apply-external-config" in english
    assert "External config writes are explicit opt-in" in english
    assert "What Engram does not write by default" in english
    assert "auto-configures **Trae**" not in english
    assert "auto-configures **Tencent CodeBuddy**" not in english
    assert "configures them, and previews your identity card" not in english
    assert "完成配置并预览你的身份卡" not in chinese
    assert "发现并配置你的 AI 工具" not in chinese
    assert "会自动配置 **Trae**" not in chinese
    assert "默认不会改写这些文件" in chinese
    assert "engram setup --apply-external-config" in chinese
    assert "写入前会先" in chinese


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


def test_cross_tool_continuity_demo_json_is_metadata_only():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "demos/cross_tool_continuity_demo.py", "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "schema",
        "isolated_store",
        "write_tool",
        "resume_tool",
        "search_tool",
        "resume_checks",
        "search_checks",
        "loop_checks",
        "loop_passed",
        "continuity",
    }
    assert payload["schema"] == 1
    assert payload["isolated_store"] is True
    assert payload["write_tool"] == "claude_code_demo"
    assert payload["resume_tool"] == "codex_demo"
    assert payload["search_tool"] == "cursor_demo"
    assert payload["loop_checks"] == {
        "write_created_demo_memory": True,
        "resume_found_recent_context": True,
        "resume_preserved_source_tool": True,
        "search_found_demo_memory": True,
        "search_preserved_source_tool": True,
    }
    assert payload["loop_passed"] is True
    assert set(payload["continuity"]) == {"readiness_level"}
    assert payload["continuity"]["readiness_level"] in {
        "cross_tool_ready",
        "observed_signals",
    }

    text = json.dumps(payload, ensure_ascii=False)
    assert "verify the signature before writing business state" not in text
    assert "The handler should keep raw event metadata" not in text
    assert "session_id" not in text
    assert "memory_body" not in text
    assert "raw_path" not in text
    assert "decision_reasoning" not in text
    assert str(Path.home()) not in text
    assert "engram-cross-tool-demo-" not in text
