"""验证 pyproject.toml、README 和 GitHub Actions 发布配置。"""

import ast
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
PYPROJECT = ROOT / "pyproject.toml"
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
MCP_SERVER = ROOT / "src" / "engram_core" / "mcp_server.py"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"
SETUP_WIZARD = ROOT / "src" / "engram_core" / "setup_wizard.py"

CORE_MCP_TOOLS = {
    "get_user_context",
    "get_identity_card",
    "search_knowledge",
    "add_lesson",
    "add_decision",
    "get_relevant_knowledge",
    "save_project_snapshot",
    "get_project_context",
    "extract_session_insights",
    "export_engram",
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
        "import engram_core.mcp_server as server\n"
        "print(json.dumps(sorted(server.mcp._tool_manager._tools.keys())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
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


def test_has_scripts_entry():
    """应有 engram CLI 入口。"""
    data = _load()
    assert data["project"].get("scripts", {}).get("engram") == "engram_core.setup_wizard:main"


def test_has_project_urls():
    """应有 Homepage URL。"""
    data = _load()
    urls = data["project"].get("urls", {})
    assert urls["Homepage"] == "https://github.com/Patdolitse/engram"
    assert "Repository" in urls
    assert "Bug Tracker" in urls


def test_has_classifiers():
    """应有 PyPI classifiers。"""
    data = _load()
    classifiers = data["project"].get("classifiers", [])
    assert len(classifiers) > 0
    assert any("Python" in classifier for classifier in classifiers)
    assert "License :: OSI Approved :: Apache Software License" in classifiers


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
    assert 'pip install -e ".[dev]"' in content
    assert "pytest tests/ -q" in content


def test_publish_workflow_exists():
    """Publish workflow 文件应存在。"""
    assert PUBLISH_WORKFLOW.is_file()


def test_publish_workflow_release_trigger_and_token():
    """Publish workflow 应在 release published 时使用 PyPI token 发布。"""
    content = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    assert "types: [published]" in content
    assert "python -m build" in content
    assert "pypa/gh-action-pypi-publish@release/v1" in content
    assert "secrets.PYPI_API_TOKEN" in content


def test_readme_uses_pypi_install_and_badge():
    """README 应展示 PyPI badge 和 piia-engram 安装命令。"""
    content = README.read_text(encoding="utf-8")
    assert "https://img.shields.io/pypi/v/piia-engram" in content
    assert "pip install piia-engram" in content
    assert "Engram exposes 37 MCP tools" in content


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

    assert "ENGRAM_TOOLS=core" in content
    assert "Tier-1 Core" in content
    assert "Tier-2 Advanced" in content
    assert "| `get_user_context` | Tier-1 Core |" in content


def test_mcp_tool_count_and_merge_tool():
    """MCP server 应暴露 36 个工具且包含合并后的统一工具。"""
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    tools = []
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
    assert len(tools) == 37
    assert "update_knowledge" in tools
    assert "bulk_add_knowledge" in tools
    assert "get_knowledge_overview" in tools
    assert "merge_knowledge" in tools
    assert "get_knowledge_inheritance" in tools
    assert "extract_session_insights" in tools
    assert "get_audit_log" in tools
    assert "get_safe_profile" not in tools
    assert "update_lesson" not in tools
    assert "update_decision" not in tools
    assert "bulk_add_lessons" not in tools
    assert "bulk_add_decisions" not in tools
    assert "get_health_report" not in tools
    assert "get_stale_knowledge" not in tools
    assert "get_knowledge_digest" not in tools


def test_mcp_tools_default_to_all_registered_tools(tmp_path: Path):
    """未设置 ENGRAM_TOOLS 时应保持全部 37 个工具，避免破坏现有用户。"""
    tools = _registered_mcp_tools(tmp_path)

    assert len(tools) == 37
    assert set(CORE_MCP_TOOLS).issubset(tools)
    assert "get_profile" in tools
    assert "bulk_add_knowledge" in tools


def test_mcp_tools_core_tier_registers_only_core_tools(tmp_path: Path):
    """ENGRAM_TOOLS=core 时应只暴露 Tier-1 核心工具。"""
    tools = _registered_mcp_tools(tmp_path, tools_tier="core")

    assert set(tools) == CORE_MCP_TOOLS
    assert "get_profile" not in tools
    assert "bulk_add_knowledge" not in tools


def test_setup_help_mentions_core_tool_tier():
    """CLI help 应提示可以用 ENGRAM_TOOLS=core 精简工具列表。"""
    content = SETUP_WIZARD.read_text(encoding="utf-8")

    assert "ENGRAM_TOOLS=core" in content
    assert "核心工具" in content


def test_zh_readme_uses_pypi_install_and_36_tools():
    """中文 README 应同步 PyPI badge、安装命令和工具数量。"""
    content = README_ZH.read_text(encoding="utf-8")
    assert "https://img.shields.io/pypi/v/piia-engram" in content
    assert "pip install piia-engram" in content
    assert "完整 MCP 工具列表（37 个）" in content
    assert "37 个 MCP 工具" in content
    assert "`bulk_add_knowledge`" in content
    assert "`update_knowledge`" in content
    assert "`get_knowledge_overview`" in content
    assert "`get_knowledge_inheritance`" in content
    assert "`merge_knowledge`" in content
    assert "`extract_session_insights`" in content
    assert "`get_safe_profile`" not in content
    assert "`bulk_add_lessons`" not in content
    assert "`bulk_add_decisions`" not in content


def test_zh_readme_documents_tool_tiering():
    """中文 README 应说明 core/all 工具分层。"""
    content = README_ZH.read_text(encoding="utf-8")

    assert "ENGRAM_TOOLS=core" in content
    assert "Tier-1 核心" in content
    assert "Tier-2 高级" in content
    assert "| `get_user_context` | Tier-1 核心 |" in content


def test_zh_readme_has_remote_deployment_section():
    """中文 README 应说明远程部署和 token 安全提醒。"""
    content = README_ZH.read_text(encoding="utf-8")
    assert "## 远程部署" in content
    assert "pip install piia-engram[remote]" in content
    assert "ENGRAM_AUTH_TOKEN" in content
    assert '"Authorization": "Bearer abc123..."' in content
    assert "数据始终在你自己的服务器上" in content
