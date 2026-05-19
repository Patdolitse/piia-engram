"""验证 pyproject.toml 和 GitHub Actions 包含 PyPI 发布所需配置。"""

from pathlib import Path

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"


def _load():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_required_fields():
    """pyproject.toml 应包含 name, version, description, license。"""
    data = _load()["project"]
    assert data["name"] == "piia-engram"
    assert data["version"] == "2.9.0"
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
