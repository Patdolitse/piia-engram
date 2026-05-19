"""验证 pyproject.toml、README 和 GitHub Actions 发布配置。"""

import ast
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


def _load():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_required_fields():
    """pyproject.toml 应包含 name, version, description, license。"""
    data = _load()["project"]
    assert data["name"] == "piia-engram"
    assert data["version"] == "3.3.0"
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
    assert "Engram exposes 36 MCP tools" in content


def test_mcp_tool_count_and_merge_tool():
    """MCP server 应暴露 36 个工具且包含合并后的统一工具。"""
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    tools = [node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
    assert len(tools) == 36
    assert "update_knowledge" in tools
    assert "bulk_add_knowledge" in tools
    assert "get_knowledge_overview" in tools
    assert "merge_knowledge" in tools
    assert "get_knowledge_inheritance" in tools
    assert "extract_session_insights" in tools
    assert "get_safe_profile" not in tools
    assert "update_lesson" not in tools
    assert "update_decision" not in tools
    assert "bulk_add_lessons" not in tools
    assert "bulk_add_decisions" not in tools
    assert "get_health_report" not in tools
    assert "get_stale_knowledge" not in tools
    assert "get_knowledge_digest" not in tools


def test_zh_readme_uses_pypi_install_and_36_tools():
    """中文 README 应同步 PyPI badge、安装命令和工具数量。"""
    content = README_ZH.read_text(encoding="utf-8")
    assert "https://img.shields.io/pypi/v/piia-engram" in content
    assert "pip install piia-engram" in content
    assert "完整 MCP 工具列表（36 个）" in content
    assert "36 个 MCP 工具" in content
    assert "`bulk_add_knowledge`" in content
    assert "`update_knowledge`" in content
    assert "`get_knowledge_overview`" in content
    assert "`get_knowledge_inheritance`" in content
    assert "`merge_knowledge`" in content
    assert "`extract_session_insights`" in content
    assert "`get_safe_profile`" not in content
    assert "`bulk_add_lessons`" not in content
    assert "`bulk_add_decisions`" not in content
