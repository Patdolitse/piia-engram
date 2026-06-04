"""Tests for scripts/check_release_auth_preflight.py.

Cover success/failure parsing of each check, the version cross-check, and the
hard security invariant: no token value is ever read into or printed from the
report. Checks are dependency-injected (which/run/env) so the suite is
deterministic regardless of what is installed on the host.
"""

from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_auth_preflight.py"
SECRET = "sk-SUPERSECRETTOKENVALUE-do-not-print"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("check_release_auth_preflight", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _which_factory(present: set[str]):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def _run_factory(rc_by_first_arg: dict[str, int], default_rc: int = 0):
    def _run(cmd, timeout=20):
        key = cmd[0]
        # twine is invoked as [python, "-m", "twine", ...]
        if "twine" in cmd:
            key = "twine"
        rc = rc_by_first_arg.get(key, default_rc)
        return rc, "", ""
    return _run


# --- server.json structural validation --------------------------------------

def _write_server_json(root: Path, version: str = "3.47.0", *, drop=None, bad=False):
    mcp_dir = root / ".mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    target = mcp_dir / "server.json"
    if bad:
        target.write_text("{not json", encoding="utf-8")
        return target
    data = {
        "name": "io.github.Example/piia-engram",
        "version": version,
        "packages": [{"registryType": "pypi", "identifier": "piia-engram"}],
    }
    if drop:
        data.pop(drop, None)
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def _write_pyproject(root: Path, version: str = "3.47.0"):
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "piia-engram"\nversion = "{version}"\n', encoding="utf-8"
    )


def test_validate_server_json_ok(mod, tmp_path):
    target = _write_server_json(tmp_path, "3.47.0")
    ok, detail, version = mod.validate_server_json(target)
    assert ok is True
    assert version == "3.47.0"


def test_validate_server_json_missing_file(mod, tmp_path):
    ok, detail, version = mod.validate_server_json(tmp_path / ".mcp" / "server.json")
    assert ok is False and "not found" in detail and version is None


def test_validate_server_json_bad_json(mod, tmp_path):
    target = _write_server_json(tmp_path, bad=True)
    ok, detail, version = mod.validate_server_json(target)
    assert ok is False and "not valid JSON" in detail


def test_validate_server_json_missing_field(mod, tmp_path):
    target = _write_server_json(tmp_path, drop="packages")
    ok, detail, version = mod.validate_server_json(target)
    assert ok is False and "packages" in detail


# --- gh / mcp-publisher / twine ---------------------------------------------

def test_github_cli_not_installed(mod):
    r = mod.check_github_cli(which=_which_factory(set()), run=_run_factory({}))
    assert r["status"] == mod.FAIL and r["required"] is True


def test_github_cli_authenticated(mod):
    r = mod.check_github_cli(
        which=_which_factory({"gh"}), run=_run_factory({"gh": 0})
    )
    assert r["status"] == mod.OK


def test_github_cli_not_authenticated(mod):
    r = mod.check_github_cli(
        which=_which_factory({"gh"}), run=_run_factory({"gh": 1})
    )
    assert r["status"] == mod.FAIL and "auth login" in r["detail"]


def test_mcp_publisher_present_absent(mod):
    assert mod.check_mcp_publisher(which=_which_factory({"mcp-publisher"}))["status"] == mod.OK
    assert mod.check_mcp_publisher(
        which=_which_factory(set()),
        candidates=[str(Path("/does-not-exist"))],
    )["status"] == mod.FAIL


def test_mcp_publisher_local_fallback_candidate(mod, tmp_path):
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")
    r = mod.check_mcp_publisher(
        which=_which_factory(set()),
        candidates=[str(local)],
    )
    assert r["status"] == mod.OK


def test_warm_mcp_registry_auth_success_no_secret_in_result(mod, tmp_path):
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")
    calls = []

    def run(cmd, timeout=20):
        calls.append(cmd)
        if cmd == ["gh", "auth", "token"]:
            return 0, SECRET, ""
        if cmd[:3] == [str(local), "login", "github"]:
            assert cmd[-2:] == ["-token", SECRET]
            assert timeout == 60
            return 0, "logged in", ""
        return 1, "", "unexpected command"

    r = mod.warm_mcp_registry_auth(
        which=_which_factory({"gh"}),
        run=run,
        candidates=[str(local)],
    )

    assert r["status"] == mod.OK
    assert SECRET not in json.dumps(r)
    assert calls[0] == ["gh", "auth", "token"]
    assert calls[1][0] == str(local)


def test_warm_mcp_registry_auth_fails_when_gh_missing(mod, tmp_path):
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")
    r = mod.warm_mcp_registry_auth(
        which=_which_factory(set()),
        run=_run_factory({}),
        candidates=[str(local)],
    )
    assert r["status"] == mod.FAIL
    assert "gh" in r["detail"]


def test_warm_mcp_registry_auth_fails_when_publisher_missing(mod):
    r = mod.warm_mcp_registry_auth(
        which=_which_factory({"gh"}),
        run=_run_factory({"gh": 0}),
        candidates=[str(Path("/does-not-exist"))],
    )
    assert r["status"] == mod.FAIL
    assert "mcp-publisher" in r["detail"]


def test_warm_mcp_registry_auth_fails_without_token(mod, tmp_path):
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")

    def run(cmd, timeout=20):
        if cmd == ["gh", "auth", "token"]:
            return 0, "   ", ""
        return 0, "", ""

    r = mod.warm_mcp_registry_auth(
        which=_which_factory({"gh"}),
        run=run,
        candidates=[str(local)],
    )
    assert r["status"] == mod.FAIL
    assert "gh auth login" in r["detail"]


def test_warm_mcp_registry_auth_fails_when_publisher_login_fails(mod, tmp_path):
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")

    def run(cmd, timeout=20):
        if cmd == ["gh", "auth", "token"]:
            return 0, SECRET, ""
        return 1, "", "login failed"

    r = mod.warm_mcp_registry_auth(
        which=_which_factory({"gh"}),
        run=run,
        candidates=[str(local)],
    )
    assert r["status"] == mod.FAIL
    assert SECRET not in json.dumps(r)


def test_twine_available_and_missing(mod):
    ok_results = mod.check_twine(run=_run_factory({"twine": 0}), env={})
    names = {r["name"]: r for r in ok_results}
    assert names["twine_available"]["status"] == mod.OK
    fail_results = mod.check_twine(run=_run_factory({"twine": 1}), env={})
    assert {r["name"]: r for r in fail_results}["twine_available"]["status"] == mod.FAIL


def test_twine_credential_presence_only(mod):
    with_cred = mod.check_twine(run=_run_factory({"twine": 0}), env={"TWINE_PASSWORD": "x"})
    cred = {r["name"]: r for r in with_cred}["pypi_credential_source"]
    assert cred["status"] == mod.OK and cred["required"] is False


def test_credential_source_value_never_read(mod, monkeypatch):
    # No env credential + no ~/.pypirc => not detected (warning).
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: Path("/nonexistent-home-xyz")))
    assert mod._has_pypi_credential_source({}) is False
    assert mod._has_pypi_credential_source({"TWINE_API_KEY": "secret"}) is True


# --- server.json + version cross-check ---------------------------------------

def test_server_json_version_match(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    results = mod.check_server_json(tmp_path, which=_which_factory(set()), run=_run_factory({}))
    by = {r["name"]: r for r in results}
    assert by["mcp_server_json"]["status"] == mod.OK
    assert by["mcp_version_match"]["status"] == mod.OK


def test_server_json_version_mismatch_blocks(mod, tmp_path):
    _write_pyproject(tmp_path, "3.48.0")
    _write_server_json(tmp_path, "3.47.0")
    results = mod.check_server_json(tmp_path, which=_which_factory(set()), run=_run_factory({}))
    by = {r["name"]: r for r in results}
    assert by["mcp_version_match"]["status"] == mod.FAIL
    assert by["mcp_version_match"]["required"] is True


def test_server_json_publisher_validate_failure(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    results = mod.check_server_json(
        tmp_path,
        which=_which_factory({"mcp-publisher"}),
        run=_run_factory({}, default_rc=1),
        candidates=[str(Path("/does-not-exist"))],
    )
    by = {r["name"]: r for r in results}
    assert by["mcp_server_json"]["status"] == mod.FAIL


def test_server_json_uses_local_fallback_publisher(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")
    calls = []

    def run(cmd, timeout=20):
        calls.append(cmd)
        return 0, "", ""

    results = mod.check_server_json(
        tmp_path,
        which=_which_factory(set()),
        run=run,
        candidates=[str(local)],
    )
    by = {r["name"]: r for r in results}
    assert by["mcp_server_json"]["status"] == mod.OK
    assert calls and calls[0][0] == str(local)


# --- aggregate + fail-closed + secret-safety --------------------------------

def test_run_preflight_all_ok(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    ok, results = mod.run_preflight(
        tmp_path,
        which=_which_factory({"gh", "mcp-publisher"}),
        run=_run_factory({"gh": 0, "mcp-publisher": 0, "twine": 0}),
        env={"TWINE_API_KEY": "x"},
    )
    assert ok is True
    assert all(r["status"] in (mod.OK, mod.SKIP) for r in results if r["required"])


def test_run_preflight_with_warm_mcp_includes_auth_result(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    local = tmp_path / "mcp-publisher.exe"
    local.write_text("stub", encoding="utf-8")

    def run(cmd, timeout=20):
        if cmd == ["gh", "auth", "status"]:
            return 0, "", ""
        if cmd == ["gh", "auth", "token"]:
            return 0, SECRET, ""
        if cmd[0] == str(local):
            return 0, "", ""
        if "twine" in cmd:
            return 0, "", ""
        return 1, "", "unexpected command"

    ok, results = mod.run_preflight(
        tmp_path,
        warm_mcp=True,
        which=_which_factory({"gh"}),
        run=run,
        env={"TWINE_API_KEY": "x"},
        mcp_publisher_candidates=[str(local)],
    )

    by = {r["name"]: r for r in results}
    assert ok is True
    assert by["mcp_registry_auth_warm"]["status"] == mod.OK
    assert SECRET not in json.dumps(results)


def test_run_preflight_fails_closed_when_gh_missing(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    ok, results = mod.run_preflight(
        tmp_path,
        which=_which_factory({"mcp-publisher"}),  # no gh
        run=_run_factory({"mcp-publisher": 0, "twine": 0}),
        env={},
    )
    assert ok is False


def test_no_secret_value_in_results_or_output(mod, tmp_path):
    _write_pyproject(tmp_path, "3.47.0")
    _write_server_json(tmp_path, "3.47.0")
    ok, results = mod.run_preflight(
        tmp_path,
        which=_which_factory({"gh", "mcp-publisher"}),
        run=_run_factory({"gh": 0, "mcp-publisher": 0, "twine": 0}),
        env={"TWINE_PASSWORD": SECRET, "TWINE_USERNAME": "__token__"},
    )
    serialized = json.dumps(results)
    assert SECRET not in serialized
    buf = io.StringIO()
    with redirect_stdout(buf):
        mod._print_human(results, ok, strict=False)
    assert SECRET not in buf.getvalue()


def test_main_setup_error_without_pyproject(mod, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert mod.main(["--root", str(tmp_path)]) == 2
