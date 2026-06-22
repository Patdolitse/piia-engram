"""Tests for scripts/bump_version.py."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = ROOT / "scripts" / "bump_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("_bump_version", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bumper():
    return _load()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_repo(root: Path, version: str) -> None:
    _write(root, "pyproject.toml", f'[project]\nversion = "{version}"\n')
    _write(root, "src/piia_engram/__init__.py", f'__version__ = "{version}"\n')
    _write(
        root,
        ".mcp/server.json",
        json.dumps(
            {
                "version": version,
                "packages": [
                    {
                        "version": version,
                        "runtimeArguments": [
                            {"name": "--from", "value": f"piia-engram=={version}"},
                            {"value": "piia-engram-mcp"},
                        ],
                    }
                ],
            },
            indent=2,
        )
        + "\n",
    )
    _write(root, ".claude-plugin/plugin.json", json.dumps({"version": version}) + "\n")
    _write(root, "glama.yaml", f"metadata:\n  version: {version}\n")
    _write(
        root,
        "docs/public-facts.json",
        json.dumps(
            {
                "local_dev_version": version,
                "last_verified_date": "2000-01-01",
                "facts": {},
            },
            indent=2,
        )
        + "\n",
    )
    _write(
        root,
        "README.md",
        f"| Version frame | **v{version}** (verified 2000-01-01) |\n",
    )
    _write(
        root,
        "README.zh-CN.md",
        f"| 版本口径 | **v{version}**（2000-01-01 已核验）|\n",
    )


def test_bump_updates_all_version_surfaces(bumper, tmp_path):
    old_version = "1.2.3"
    new_version = "1.2.4"
    today = "2099-01-02"
    _fixture_repo(tmp_path, old_version)

    result = bumper.bump_version(tmp_path, new_version, today=today, verify=False)

    assert result.changed_files == [
        "pyproject.toml",
        "src/piia_engram/__init__.py",
        ".mcp/server.json",
        ".claude-plugin/plugin.json",
        "glama.yaml",
        "docs/public-facts.json",
        "README.md",
        "README.zh-CN.md",
    ]
    assert f'version = "{new_version}"' in (
        tmp_path / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert f'__version__ = "{new_version}"' in (
        tmp_path / "src/piia_engram/__init__.py"
    ).read_text(encoding="utf-8")

    server = json.loads((tmp_path / ".mcp/server.json").read_text(encoding="utf-8"))
    assert server["version"] == new_version
    assert server["packages"][0]["version"] == new_version
    assert server["packages"][0]["runtimeArguments"][0]["value"] == (
        f"piia-engram=={new_version}"
    )

    plugin = json.loads(
        (tmp_path / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    assert plugin["version"] == new_version
    assert f"version: {new_version}" in (
        tmp_path / "glama.yaml"
    ).read_text(encoding="utf-8")

    facts = json.loads(
        (tmp_path / "docs/public-facts.json").read_text(encoding="utf-8")
    )
    assert facts["local_dev_version"] == new_version
    assert facts["last_verified_date"] == today
    assert f"**v{new_version}**" in (
        tmp_path / "README.md"
    ).read_text(encoding="utf-8")
    assert f"**v{new_version}**" in (
        tmp_path / "README.zh-CN.md"
    ).read_text(encoding="utf-8")


def test_bump_is_idempotent(bumper, tmp_path):
    old_version = "2.3.3"
    new_version = "2.3.4"
    today = "2099-01-02"
    _fixture_repo(tmp_path, old_version)

    first = bumper.bump_version(tmp_path, new_version, today=today, verify=False)
    snapshot = {
        path: (tmp_path / path).read_text(encoding="utf-8")
        for path in (
            "pyproject.toml",
            "src/piia_engram/__init__.py",
            ".mcp/server.json",
            ".claude-plugin/plugin.json",
            "glama.yaml",
            "docs/public-facts.json",
            "README.md",
            "README.zh-CN.md",
        )
    }
    second = bumper.bump_version(tmp_path, new_version, today=today, verify=False)

    assert first.changed_files
    assert second.changed_files == []
    assert {
        path: (tmp_path / path).read_text(encoding="utf-8") for path in snapshot
    } == snapshot


@pytest.mark.parametrize(
    "bad_version",
    ["v1.2.3", "1.2", "1.2.3.4", "1.2.x", "1.2.3-beta", " 1.2.3"],
)
def test_invalid_version_is_rejected(bumper, tmp_path, bad_version):
    _fixture_repo(tmp_path, "1.2.3")

    with pytest.raises(ValueError, match="semver"):
        bumper.bump_version(tmp_path, bad_version, verify=False)
