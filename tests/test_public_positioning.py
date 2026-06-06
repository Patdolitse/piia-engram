"""Regression tests for public positioning, listing copy, and demos."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import ast
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _mcp_tool_names() -> set[str]:
    tree = ast.parse(_read("src/piia_engram/mcp_server.py"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool"
            ):
                names.add(node.name)
    return names


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

    # Anchor on pyproject (the single source of truth) and assert every other
    # public surface agrees, instead of pinning a literal that rots each
    # release. Format is still validated, just not hardcoded.
    version = pyproject["project"]["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    assert server["version"] == version
    assert server["packages"][0]["version"] == version
    assert plugin["version"] == version
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
        "docs/trust-evidence.md",
        "docs/quickstart-first-value.md",
        "docs/tool-surface-analysis.md",
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
        "docs/trust-evidence.md",
        "docs/context-governance.md",
        "docs/quickstart-first-value.md",
        "docs/tool-surface-analysis.md",
        "docs/listing-copy.md",
        "docs/cross-tool-continuity-demo.md",
        "docs/runbooks/agent-client-validation.md",
    ]:
        assert path in allowlist


def test_public_evidence_doc_is_outsider_legible_and_bounded():
    doc = _read("docs/trust-evidence.md")

    for phrase in [
        "How to read this page",
        "Claim",
        "Evidence",
        "Check it yourself",
        "What this evidence does not prove",
        "not a security audit",
        "not a live-agent benchmark",
        "python scripts/check_public_trust_claims.py",
        "python scripts/run_memory_evals.py",
    ]:
        assert phrase in doc

    for forbidden in [
        "state-of-the-art",
        "beats Mem0",
        "outperforms Letta",
        "proves live model compliance",
    ]:
        assert forbidden not in doc


def test_context_governance_doc_is_proposal_only_and_bounded():
    doc = _read("docs/context-governance.md")

    for phrase in [
        "local proposal",
        "do not publish",
        "preview_context_governance",
        "applied: false",
        "context_governance_preview_only",
        "safe_context",
        "freshness_conflicts",
        "replay_packet",
        "external_evidence",
        "owner confirmation",
    ]:
        assert phrase in doc

    for forbidden in [
        "automatically publish",
        "automatically archive",
        "release approval",
        "pushes to GitHub",
    ]:
        assert forbidden not in doc


def test_quickstart_first_value_stays_core_and_honest():
    doc = _read("docs/quickstart-first-value.md")

    for phrase in [
        "Goal",
        "17 core tools",
        "ENGRAM_TOOLS=core",
        "ENGRAM_TOOLS=all",
        "staging",
        "verified only after you approve",
        "get_user_context",
        "search_knowledge",
        "add_lesson",
        "doctor",
        "owner-gated export surface",
        "If recall did not fire",
        "L2 read/search capable",
        "integrations/claude-code.md",
        "operator-mcp-cheatsheet.md",
    ]:
        assert phrase in doc

    for forbidden in [
        "every MCP client",
        "works with every AI tool",
        "verified immediately",
        "under 30 seconds",
        "is L4 behavior-verified",
    ]:
        assert forbidden not in doc


def test_supported_tools_table_uses_evidence_levels_not_bare_verified():
    readme = _read("README.md")
    readme_zh = _read("README.zh-CN.md")

    assert "Evidence status" in readme
    assert "L4 partial continuity proof" in readme
    assert "L2 setup/read-search evidence path" in readme
    assert "L3 static file-bridge evidence" in readme
    assert "4 verified + 9 expected-to-work" not in readme
    assert "| Claude Code | MCP over stdio | ✅ Verified |" not in readme
    assert "| Cursor | MCP over stdio | ✅ Verified |" not in readme

    assert "证据状态" in readme_zh
    assert "L4 部分连续性证明" in readme_zh
    assert "L3 静态文件桥证据" in readme_zh
    assert "4 已验证 + 9 应兼容" not in readme_zh


def test_operator_mcp_cheatsheet_matches_tool_surface_json():
    doc = _read("docs/operator-mcp-cheatsheet.md")
    surface = json.loads(_read("docs/mcp-tool-surface.json"))

    assert surface["operator_notes"]["core_is_not_read_only"]
    assert surface["operator_notes"]["all_tools_mode"]
    assert "Core means high-frequency" in doc
    assert "It does not mean read-only" in doc
    assert "get_identity_card" in doc
    assert "ENGRAM_TOOLS=all" in doc
    assert "evidence_readiness" in doc


def test_tool_surface_analysis_covers_all_current_tools_without_refactor_claims():
    doc = _read("docs/tool-surface-analysis.md")
    tools = _mcp_tool_names()

    assert len(tools) == 84
    assert "17 core tools" in doc
    assert "67 advanced tools" in doc
    assert "Core is not read-only" in doc
    assert "core but owner-gated" in doc
    assert "Optional local / dogfood tools" in doc
    assert "Internal maintenance / legacy tools" in doc
    assert "Release posture by bucket" in doc
    assert "Deprecated compatibility read" in doc
    assert "analysis only" in doc.lower()
    for name in sorted(tools):
        assert f"`{name}`" in doc, name


def test_public_tool_surface_docs_label_owner_local_and_legacy_tools():
    readme = _read("README.md")
    readme_zh = _read("README.zh-CN.md")
    analysis = _read("docs/tool-surface-analysis.md")
    tools_ref = _read("skills/engram/references/tools.md")
    glama = _read("glama.yaml")
    architecture = _read("docs/architecture.md")

    for phrase in [
        'Core means "used in most sessions", not "read-only"',
        "Owner-gated export: write and return a Markdown identity card",
        "Optional local integration governed write: register",
        "Deprecated compatibility read; prefer `get_preferences`",
        "Owner maintenance: dry-run project/global/shared scope suggestions",
        "Advanced owner-gated preview: build safe-context",
        "Internal/dogfood",
    ]:
        assert phrase in readme

    for phrase in [
        "核心工具表示",
        "不表示",
        "只读安全集合",
        "owner-gated 导出",
        "可选本地集成",
        "deprecated 兼容读取",
    ]:
        assert phrase in readme_zh

    for phrase in [
        "Core is not read-only",
        "Owner/export",
        "Optional local / dogfood tools",
        "Internal maintenance / legacy tools",
        "preview_context_governance",
    ]:
        assert phrase in analysis

    for phrase in [
        "high-frequency surface, not a read-only guarantee",
        "owner-gated export/identity",
        "optional local",
        "owner/admin/export surfaces",
        "read_web_content",
        "preview_context_governance",
    ]:
        assert phrase in tools_ref

    assert "Owner-gated export of a markdown identity card" in glama
    assert "Optional local integration" in glama
    assert "Tier-1 is a discoverability and context-budget tier" in architecture
    assert "writes an owner-gated export file" in architecture


def test_comparison_disambiguates_same_name_engram_without_overclaim():
    doc = _read("docs/comparison.md")

    assert "Gentleman-Programming/engram" in doc
    assert "unrelated" in doc
    assert "single Go binary" in doc
    assert "user-owned identity layer" in doc
    assert "better than Gentleman" not in doc


def test_architecture_does_not_carry_stale_mcp_wrapper_count():
    doc = _read("docs/architecture.md")

    assert "81 `@mcp.tool()`" not in doc
    assert "83 `@mcp.tool()`" in doc


def test_agent_client_validation_runbook_is_purpose_first():
    doc = _read("docs/runbooks/agent-client-validation.md")

    for phrase in [
        "Every test must be purpose-first",
        "Purpose: What question does this test answer?",
        "Decision use: What decision will this result support?",
        "Not proven: What should nobody claim from this result?",
        "OPTIMIZATION_NOTES.md",
        "Optimization Notes Template",
    ]:
        assert phrase in doc

    for case in [f"T{i}" for i in range(1, 11)]:
        assert case in doc

    for client in ["Cursor Agent", "Hermes", "OpenClaw-Compatible Flows"]:
        assert client in doc


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
