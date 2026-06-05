"""Regression tests for release-flow failure recovery helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    script = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(script.stem, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def workflow_order():
    return _load_script("check_publish_workflow_order.py")


@pytest.fixture(scope="module")
def pypi_fallback():
    return _load_script("publish_pypi_fallback.py")


@pytest.fixture(scope="module")
def mcp_publish():
    return _load_script("publish_mcp_registry.py")


@pytest.fixture(scope="module")
def mcp_verify():
    return _load_script("verify_mcp_registry_version.py")


def test_publish_workflow_order_accepts_installed_deps_before_gates(workflow_order):
    text = """
      - name: Set up Python
        uses: actions/setup-python@v6
      - name: Install project dependencies for release gates
        run: pip install -e .
      - name: Export redaction sample gate
        run: python scripts/check_export_redaction.py --strict docs/samples/export-redaction-clean-sample.md
    """

    ok, problems = workflow_order.check_publish_workflow_order(text)

    assert ok is True
    assert problems == []


def test_publish_workflow_order_rejects_gate_before_deps(workflow_order):
    text = """
      - name: Export redaction sample gate
        run: python scripts/check_export_redaction.py --strict docs/samples/export-redaction-clean-sample.md
      - name: Install project dependencies for release gates
        run: pip install -e .
    """

    ok, problems = workflow_order.check_publish_workflow_order(text)

    assert ok is False
    assert "before dependency install" in problems[0]


def test_pypi_fallback_sets_utf8_env_and_disables_progress_bar(pypi_fallback):
    cmd = pypi_fallback.build_twine_upload_command(["dist/*"], python="PY")
    env = pypi_fallback.build_upload_env({"PYTHONIOENCODING": "cp936"})

    assert cmd[:4] == ["PY", "-m", "twine", "upload"]
    assert "--disable-progress-bar" in cmd
    assert "--skip-existing" in cmd
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["NO_COLOR"] == "1"


def test_pypi_fallback_expands_globs_without_shell(pypi_fallback, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "a.whl").write_text("wheel", encoding="utf-8")
    (dist / "b.tar.gz").write_text("sdist", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cmd = pypi_fallback.build_twine_upload_command(["dist/*"], python="PY")

    assert [Path(item).as_posix() for item in cmd[-2:]] == [
        "dist/a.whl",
        "dist/b.tar.gz",
    ]


def test_mcp_publish_retries_once_on_expired_jwt_without_printing_token(mcp_publish):
    calls = []
    secret = "gho_SECRET_DO_NOT_PRINT"

    def run(cmd, timeout=60):
        calls.append(cmd)
        if cmd[:2] == ["publisher", "publish"] and len(calls) == 1:
            return 1, "", "server returned status 401: Invalid or expired Registry JWT token"
        if cmd == ["gh", "auth", "token"]:
            return 0, secret, ""
        if cmd[:3] == ["publisher", "login", "github"]:
            assert cmd[-2:] == ["-token", secret]
            return 0, "logged in", ""
        if cmd[:2] == ["publisher", "publish"]:
            return 0, "published", ""
        return 99, "", "unexpected"

    ok, events = mcp_publish.publish_with_retry(
        ".mcp/server.json",
        publisher="publisher",
        run=run,
    )

    assert ok is True
    assert [event["step"] for event in events] == ["publish", "gh_token", "login", "publish"]
    assert secret not in str(events)


def test_mcp_publish_does_not_retry_non_auth_failure(mcp_publish):
    calls = []

    def run(cmd, timeout=60):
        calls.append(cmd)
        return 2, "", "validation failed"

    ok, events = mcp_publish.publish_with_retry(
        ".mcp/server.json",
        publisher="publisher",
        run=run,
    )

    assert ok is False
    assert len(calls) == 1
    assert events == [{"step": "publish", "rc": 2, "retry": False}]


def test_mcp_registry_verify_follows_next_cursor(mcp_verify):
    urls = []

    def fetch(url):
        urls.append(url)
        if len(urls) == 1:
            return {
                "servers": [
                    {"server": {"name": "io.github.Patdolitse/piia-engram", "version": "3.49.2"}}
                ],
                "metadata": {"nextCursor": "io.github.Patdolitse/piia-engram:3.49.2"},
            }
        return {
            "servers": [
                {
                    "server": {
                        "name": "io.github.Patdolitse/piia-engram",
                        "version": "3.50.0",
                        "packages": [{"version": "3.50.0"}],
                    },
                    "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}},
                }
            ],
            "metadata": {},
        }

    found = mcp_verify.find_registry_version(
        name="io.github.Patdolitse/piia-engram",
        version="3.50.0",
        api="https://registry.example/v0/servers",
        fetch=fetch,
    )

    assert found is not None
    assert found["server"]["version"] == "3.50.0"
    assert "cursor=" in urls[1]


def test_mcp_registry_verify_returns_none_when_absent(mcp_verify):
    found = mcp_verify.find_registry_version(
        name="io.github.Patdolitse/piia-engram",
        version="9.9.9",
        fetch=lambda url: {"servers": [], "metadata": {}},
    )

    assert found is None
