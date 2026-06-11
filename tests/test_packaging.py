"""验证 pyproject.toml、README 和 GitHub Actions 发布配置。"""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
ARCHITECTURE = ROOT / "docs" / "architecture.md"
GLAMA_YAML = ROOT / "glama.yaml"
MCP_SERVER = ROOT / "src" / "piia_engram" / "mcp_server.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
SETUP_WIZARD = ROOT / "src" / "piia_engram" / "setup_wizard.py"

CORE_MCP_TOOLS = {
    "get_user_context",
    "wrap_up_session",
    "memory_store",
    "add_lesson",
    "add_decision",
    "add_playbook",
    "search_knowledge",
    "get_relevant_knowledge",
    "get_recall",
    "get_identity_card",
    "update_identity",
    "get_project_context",
    "save_project_snapshot",
    "get_recent_context",
    "get_daily_log",       # v3.30 mechanism (5)
    "get_resume_brief",    # v3.30 mechanism (3)
    "doctor",
}


def _load():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _registered_mcp_tools(tmp_path: Path, tools_tier: str | None = None) -> list[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["ENGRAM_DIR"] = str(tmp_path / "engram")
    if tools_tier is None:
        env.pop("ENGRAM_TOOLS", None)
    else:
        env["ENGRAM_TOOLS"] = tools_tier

    script = (
        "import json\n"
        "import piia_engram.mcp_server as server\n"
        "print(json.dumps(sorted(server.mcp._tool_manager._tools.keys())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_required_fields():
    """pyproject.toml 应包含 name, version, description, license。"""
    data = _load()["project"]
    assert data["name"] == "piia-engram"
    assert data["version"]  # version exists and is non-empty
    assert data["description"]
    assert data["license"]
    assert data["authors"]
    assert data["keywords"]


def test_version_consistency():
    """版本号四处必须一致：pyproject / __init__ / server.json(顶层 + packages[0])。

    v3.30.1 发版时 __init__.py 漏同步（停在 3.30.0.dev0），靠人工易漏。
    这个测试让 CI 自动拦截"版本号四处不同步"，发版只需改全四处即可绿。
    """
    pyproject_version = _load()["project"]["version"]

    # __init__.py __version__
    init_text = (ROOT / "src" / "piia_engram" / "__init__.py").read_text(
        encoding="utf-8"
    )
    import re
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_text)
    assert m, "__version__ not found in __init__.py"
    init_version = m.group(1)

    # .mcp/server.json — top-level + packages[0]
    server = json.loads(
        (ROOT / ".mcp" / "server.json").read_text(encoding="utf-8")
    )
    server_top = server.get("version")
    server_pkg = server.get("packages", [{}])[0].get("version")

    assert init_version == pyproject_version, (
        f"__init__.py ({init_version}) != pyproject ({pyproject_version})"
    )
    assert server_top == pyproject_version, (
        f"server.json top ({server_top}) != pyproject ({pyproject_version})"
    )
    assert server_pkg == pyproject_version, (
        f"server.json packages[0] ({server_pkg}) != pyproject ({pyproject_version})"
    )


def test_glama_metadata_tracks_current_public_version_and_tool_count():
    """Glama listing metadata should not lag behind release metadata."""
    pyproject_version = _load()["project"]["version"]
    content = GLAMA_YAML.read_text(encoding="utf-8")

    assert f"version: {pyproject_version}" in content
    assert "53 MCP tools" in content
    assert "'core' (17 tools)" in content
    assert "'all' (53 tools)" in content
    assert "87 MCP tools" not in content
    assert "'all' (87 tools)" not in content
    assert "'core' (12 tools)" not in content


def test_has_scripts_entry():
    """应有 engram CLI 入口。"""
    data = _load()
    assert data["project"].get("scripts", {}).get("engram") == "piia_engram.setup_wizard:main"
    assert data["project"].get("scripts", {}).get("piia-engram-mcp") == "piia_engram.mcp_server:main"


def test_mcp_console_entry_target_is_callable():
    """The packaged MCP console script must point at an importable callable."""
    import piia_engram.mcp_server as mcp_server

    assert callable(mcp_server.main)


def test_has_project_urls():
    """应有 Homepage URL。"""
    data = _load()
    urls = data["project"].get("urls", {})
    assert urls["Homepage"] == "https://github.com/Patdolitse/piia-engram"
    assert "Repository" in urls
    assert "Bug Tracker" in urls


def test_has_classifiers():
    """应有 PyPI classifiers。"""
    data = _load()
    classifiers = data["project"].get("classifiers", [])
    assert len(classifiers) > 0
    assert any("Python" in classifier for classifier in classifiers)
    assert data["project"]["license"] == "AGPL-3.0-or-later"


def test_dev_dependency_has_tomli_for_python310():
    """Python 3.10 运行 packaging 测试时应有 tomli fallback。"""
    data = _load()
    dev_deps = data["project"]["optional-dependencies"]["dev"]
    assert 'tomli>=2.0; python_version < "3.11"' in dev_deps


def test_remote_optional_dependency_has_uvicorn():
    """remote extras 应声明 SSE 服务运行所需的 uvicorn。"""
    data = _load()
    remote_deps = data["project"]["optional-dependencies"]["remote"]
    assert "uvicorn>=0.20" in remote_deps


def test_secure_optional_dependency_has_cryptography():
    """secure extras 应声明加密所需的 cryptography。"""
    data = _load()
    secure_deps = data["project"]["optional-dependencies"]["secure"]
    assert "cryptography>=41.0" in secure_deps


def test_all_optional_dependency():
    """all extras 应包含 remote + secure 的所有依赖。"""
    data = _load()
    all_deps = data["project"]["optional-dependencies"]["all"]
    assert "uvicorn>=0.20" in all_deps
    assert "cryptography>=41.0" in all_deps


def test_ci_workflow_exists():
    """CI workflow 文件应存在。"""
    assert CI_WORKFLOW.is_file()


def test_ci_workflow_matrix():
    """CI workflow 应覆盖 3 OS x 3 Python 版本。"""
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "ubuntu-latest" in content
    assert "macos-latest" in content
    assert "windows-latest" in content
    assert '"3.10"' in content
    assert '"3.11"' in content
    assert '"3.12"' in content
    assert '"3.13"' in content
    assert 'pip install -e ".[dev]"' in content
    assert "pytest tests/ -q" in content


def test_publish_workflow_exists():
    """Publish workflow 文件应存在。"""
    assert PUBLISH_WORKFLOW.is_file()


def test_publish_workflow_release_trigger_and_trusted_publishing():
    """Publish workflow should use pure PyPI Trusted Publishing, not token fallback."""
    content = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "types: [published]" in content
    assert "id-token: write" in content
    assert "environment: pypi" in content
    assert "check_public_fact_sync.py" in content
    assert "release_sanitize_check.py --internal --strict" in content
    assert "python -m build" in content
    assert "check_release_artifact_private_terms.py dist --strict" in content
    assert "pypa/gh-action-pypi-publish@release/v1" in content
    assert "skip-existing: true" in content
    assert "secrets.PYPI_API_TOKEN" not in content
    assert "password:" not in content
    assert "username:" not in content


def test_ci_workflow_runs_public_fact_sync_gate():
    """CI should catch README/version/tool/test drift before release prep."""
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "CI-like pytest entrypoint gate" in content
    assert "python scripts/check_ci_pytest_entrypoint.py --discover-script-imports" in content
    assert "Public fact sync gate" in content
    assert "python scripts/check_public_fact_sync.py" in content


def test_ci_workflow_runs_claim_drift_and_export_redaction_gates():
    """CI should catch public overclaims and generated export leaks early."""
    content = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "Publish workflow order gate" in content
    assert "python scripts/check_publish_workflow_order.py" in content
    assert "Public claim drift gate" in content
    assert "python scripts/check_public_claim_drift.py" in content
    assert "Export redaction sample gate" in content
    assert "python scripts/check_export_redaction.py --strict" in content
    assert "Generated export redaction gate" in content
    assert "python scripts/check_generated_export_redaction.py" in content


def test_publish_workflow_runs_claim_drift_and_export_redaction_gates():
    """Publish workflow should repeat public/drift and export-boundary checks."""
    content = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "Publish workflow order gate" in content
    assert "python scripts/check_publish_workflow_order.py" in content
    assert content.index("python scripts/check_publish_workflow_order.py") < content.index("pip install -e .")
    assert content.index("pip install -e .") < content.index("python scripts/check_export_redaction.py")
    assert "Public claim drift gate" in content
    assert "python scripts/check_public_claim_drift.py" in content
    assert "Export redaction sample gate" in content
    assert "python scripts/check_export_redaction.py --strict" in content
    assert "Generated export redaction gate" in content
    assert "python scripts/check_generated_export_redaction.py" in content


def test_readme_uses_pypi_install_and_badge():
    """README 应展示 PyPI badge 和 piia-engram 安装命令。"""
    content = README.read_text(encoding="utf-8")
    assert "https://img.shields.io/pypi/v/piia-engram" in content
    assert "pip install piia-engram" in content
    # Don't hardcode the tool count — just verify the claim exists
    assert "MCP tools" in content


def test_readme_has_remote_deployment_section():
    """README 应说明远程部署和 Bearer token 配置。"""
    content = README.read_text(encoding="utf-8")
    assert "## Remote Deployment" in content
    assert "pip install piia-engram[remote]" in content
    assert "ENGRAM_AUTH_TOKEN" in content
    assert '"Authorization": "Bearer abc123..."' in content


def test_readme_documents_tool_tiering():
    """English README should explain the core/all MCP tool tiers."""
    content = README.read_text(encoding="utf-8")

    assert "ENGRAM_TOOLS=all" in content
    assert "Tier-1 Core" in content
    assert "Tier-2 Advanced" in content
    assert "`get_user_context`" in content
    assert "`wrap_up_session`" in content


def test_mcp_tool_count_and_merge_tool():
    """MCP server 应暴露完整工具集合，包含 v3.30 新增工具。"""
    tools = []
    files = [MCP_SERVER, *sorted(MCP_SERVER.parent.glob("mcp_tools_*.py"))]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    tools.append(node.name)
                    break
    # v4.0.0 consolidation: 87 tools merged down to 53 (17 core + 36
    # advanced). The floor catches silent removals; adding tools is fine,
    # removing one must be deliberate (exact split is pinned in
    # tests/test_count_mcp_tools.py).
    assert len(tools) >= 53, (
        f"Expected >=53 @mcp.tool() definitions, found {len(tools)}. "
        "If a tool was removed intentionally, update this floor."
    )
    assert "update_knowledge" in tools
    assert "get_knowledge_overview" in tools
    assert "merge_knowledge" in tools
    assert "get_knowledge_inheritance" in tools
    assert "extract_session_insights" in tools
    assert "get_audit_log" in tools
    assert "get_stale_knowledge" in tools
    # v4.0.0 merged tools — pin them explicitly so an accidental removal
    # surfaces here before users notice in a release.
    assert "memory_store" in tools
    assert "get_identity_facets" in tools
    assert "user_portrait" in tools
    assert "get_playbooks" in tools
    assert "manage_playbook" in tools
    assert "playbook_execution" in tools
    assert "explore_knowledge" in tools
    assert "manage_relation" in tools
    assert "review_staging" in tools
    assert "manage_caller_trust" in tools
    # v3.30 newcomers — still pinned.
    assert "get_resume_brief" in tools
    assert "get_daily_log" in tools
    assert "preview_context_governance" in tools
    # Deleted pre-v4 names must not resurface (a representative subset of
    # the 42 removed registrations; the full mapping lives in the
    # migration guide).
    for legacy in (
        "get_profile",
        "get_preferences",
        "get_work_style",
        "get_playbook",
        "get_recent_playbooks",
        "list_playbooks_for_management",
        "update_playbook",
        "archive_playbook",
        "delete_playbook",
        "restore_playbook",
        "prepare_playbook_execution",
        "update_execution_step",
        "get_execution_status",
        "bulk_add_knowledge",
        "review_knowledge",
        "apply_review",
        "list_pending_staging",
        "batch_review_staging",
        "link_knowledge",
        "unlink_knowledge",
        "add_relation",
        "remove_relation",
        "get_related_knowledge",
        "find_similar_knowledge",
        "suggest_merges",
        "get_decision_thread",
        "get_decision_history",
        "get_user_portrait",
        "save_user_portrait",
        "compare_user_portraits",
        "export_engram_to_openclaw",
        "import_engram_from_openclaw",
        "set_caller_trust",
        "revoke_caller",
        "classify_legacy_playbooks",
        "resolve_playbook_scope_review",
    ):
        assert legacy not in tools, f"legacy tool {legacy} resurfaced"
    assert "get_safe_profile" not in tools
    assert "update_lesson" not in tools
    assert "update_decision" not in tools
    assert "bulk_add_lessons" not in tools
    assert "bulk_add_decisions" not in tools
    assert "get_health_report" not in tools
    assert "get_knowledge_digest" not in tools


def test_source_and_user_facing_docs_are_utf8_without_bom():
    """Source and public docs must not gain a UTF-8 BOM during Windows edits."""
    checked = [
        *sorted((ROOT / "src" / "piia_engram").rglob("*.py")),
        README,
        README_ZH,
        ARCHITECTURE,
    ]
    for path in checked:
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), (
            f"{path.relative_to(ROOT)} starts with a UTF-8 BOM"
        )


def test_architecture_documents_current_tool_split():
    """Architecture docs should carry the same 53/17/36 tool split as README."""
    content = ARCHITECTURE.read_text(encoding="utf-8")
    assert re.search(r"\b53 tools\b", content)
    assert "17 Tier-1" in content
    assert "36 Tier-2" in content
    assert not re.search(r"\b87 tools\b", content)
    assert "70 Tier-2" not in content


def test_cli_help_documents_current_tool_split():
    """CLI help text should not drift from the public 53/17 tool split."""
    content = SETUP_WIZARD.read_text(encoding="utf-8")
    assert "17 核心工具 / core MCP tools" in content
    assert "unlock all 53 tools" in content
    assert "unlock all 87 tools" not in content


def test_mcp_tools_default_to_core_tier(tmp_path: Path):
    """未设置 ENGRAM_TOOLS 时默认只加载 Tier-1 核心工具。"""
    tools = _registered_mcp_tools(tmp_path)

    assert set(tools) == CORE_MCP_TOOLS
    assert "get_identity_facets" not in tools
    assert "review_staging" not in tools


def test_mcp_tools_all_tier_registers_all_tools(tmp_path: Path):
    """ENGRAM_TOOLS=all 时应暴露全部工具（含 v4.0 合并工具）。"""
    tools = _registered_mcp_tools(tmp_path, tools_tier="all")

    assert len(tools) >= 53
    assert set(CORE_MCP_TOOLS).issubset(tools)
    assert "get_identity_facets" in tools
    assert "manage_playbook" in tools
    assert "review_staging" in tools
    assert "get_stale_knowledge" in tools
    assert "get_profile" not in tools
    assert "bulk_add_knowledge" not in tools
    assert "review_knowledge" not in tools


def test_setup_help_mentions_tool_tiers():
    """CLI help 应提示可以用 ENGRAM_TOOLS=all 解锁全部工具。"""
    content = SETUP_WIZARD.read_text(encoding="utf-8")

    assert "ENGRAM_TOOLS=all" in content
    assert "核心工具" in content


def test_zh_readme_uses_pypi_install_and_current_tool_split():
    """中文 README 应同步 PyPI badge、安装命令和工具数量。"""
    content = README_ZH.read_text(encoding="utf-8")
    assert "https://img.shields.io/pypi/v/piia-engram" in content
    assert "pip install piia-engram" in content
    assert "36 个" in content  # Tier-2 tool count
    assert "17 个" in content  # Tier-1 tool count
    assert "70 个" not in content  # pre-v4 Tier-2 count must not linger
    assert "`update_knowledge`" in content
    assert "`get_knowledge_overview`" in content
    assert "`get_knowledge_inheritance`" in content
    assert "`merge_knowledge`" in content
    assert "`get_stale_knowledge`" in content
    assert "`extract_session_insights`" in content
    # v4.0 merged tools documented; deleted names gone.
    assert "`get_identity_facets`" in content
    assert "`manage_playbook`" in content
    assert "`playbook_execution`" in content
    assert "`explore_knowledge`" in content
    assert "`manage_relation`" in content
    assert "`review_staging`" in content
    assert "`bulk_add_knowledge`" not in content
    assert "`review_knowledge`" not in content
    assert "`link_knowledge`" not in content
    assert "`get_safe_profile`" not in content
    assert "`bulk_add_lessons`" not in content
    assert "`bulk_add_decisions`" not in content


def test_zh_readme_documents_tool_tiering():
    """中文 README 应说明 core/all 工具分层。"""
    content = README_ZH.read_text(encoding="utf-8")

    assert "ENGRAM_TOOLS=all" in content
    assert "Tier-1 核心" in content
    assert "Tier-2 高级" in content
    assert "`get_user_context`" in content
    assert "`wrap_up_session`" in content


def test_readmes_document_playbook_scope_management_cli():
    readme = README.read_text(encoding="utf-8")
    readme_zh = README_ZH.read_text(encoding="utf-8")
    assert "management action playbook_scope accept_project" in readme
    assert "management action playbook_scope accept_project" in readme_zh
    assert "management action playbook_scope accept_shared" in readme
    assert "management action playbook_scope accept_shared" in readme_zh
    # v4.0: legacy scope migration left the MCP surface for the owner CLI.
    assert "engram playbook scope" in readme
    assert "engram playbook scope" in readme_zh


def test_playbook_docs_preserve_passive_reference_and_outcome_contract():
    readme = README.read_text(encoding="utf-8")
    readme_zh = README_ZH.read_text(encoding="utf-8")
    trust = (ROOT / "docs" / "trust.md").read_text(encoding="utf-8")
    mcp_server = MCP_SERVER.read_text(encoding="utf-8")

    assert "passive reference" in readme
    assert "outcome rollup" in readme
    assert "required_tools" in readme
    assert "resolved_tools" in readme
    assert "does not silently execute" in trust
    assert "does not store resolved local tool paths" in trust
    assert "被动参考" in readme_zh
    assert "结果汇总" in readme_zh
    assert "required_tools" in readme_zh
    assert "resolved_tools" in readme_zh
    assert "_PLAYBOOK_USAGE_POLICY" in mcp_server
    assert "the AI finds the playbook and follows it" not in readme
    assert "Generate an executable plan" not in readme
    assert "return an executable plan" not in mcp_server
    assert "直接按流程走" not in readme_zh
    assert "生成可执行的操作计划" not in readme_zh


def test_zh_readme_has_remote_deployment_section():
    """中文 README 应说明远程部署和 token 安全提醒。"""
    content = README_ZH.read_text(encoding="utf-8")
    assert "## 远程部署" in content
    assert "pip install piia-engram[remote]" in content
    assert "ENGRAM_AUTH_TOKEN" in content
    assert '"Authorization": "Bearer abc123..."' in content
    assert "数据始终在你自己的服务器上" in content


def test_fastmcp_instructions_include_lifecycle_contract():
    """FastMCP instructions 应包含 provider lifecycle 关键术语。"""
    source = MCP_SERVER.read_text(encoding="utf-8")
    # Find the FastMCP instructions string
    assert "STARTUP" in source, "instructions should mention STARTUP phase"
    assert "RETRIEVAL" in source, "instructions should mention RETRIEVAL phase"
    assert "WRITEBACK" in source, "instructions should mention WRITEBACK phase"
    assert "SESSION END" in source, "instructions should mention SESSION END phase"
    assert "memory_store" in source, "instructions should recommend memory_store"
