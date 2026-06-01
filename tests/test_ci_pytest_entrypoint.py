"""Tests for scripts/check_ci_pytest_entrypoint.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_ci_pytest_entrypoint.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_ci_pytest_entrypoint", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ci_entry():
    return _load()


def test_default_targets_include_cross_tool_resume_benchmark(ci_entry):
    assert ci_entry.DEFAULT_TARGETS == ("tests/test_cross_tool_resume_benchmark.py",)


def test_resolve_targets_rejects_paths_outside_repo(ci_entry, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside_test.py"
    outside.write_text("def test_noop(): pass\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside repository"):
        ci_entry.resolve_targets(root, [str(outside)])


def test_build_pytest_command_uses_absolute_targets(ci_entry, tmp_path):
    root = tmp_path / "repo"
    target = root / "tests" / "test_example.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_noop(): pass\n", encoding="utf-8")

    cmd = ci_entry.build_pytest_command(
        root,
        ["tests/test_example.py"],
        python_executable="python-test",
    )

    assert cmd[:3] == ["python-test", "-m", "pytest"]
    assert str(target.resolve()) in cmd
    assert "-q" in cmd


def test_clean_env_removes_pythonpath(ci_entry):
    env = ci_entry.clean_env({"PYTHONPATH": "src", "OTHER": "1"})

    assert "PYTHONPATH" not in env
    assert env["OTHER"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_run_ci_pytest_entrypoint_uses_temp_cwd_and_clean_env(
    ci_entry, tmp_path, monkeypatch,
):
    root = tmp_path / "repo"
    target = root / "tests" / "test_example.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_noop(): pass\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    calls = {}

    class FakeTempDir:
        def __init__(self, prefix):
            calls["prefix"] = prefix

        def __enter__(self):
            return str(outside)

        def __exit__(self, exc_type, exc, tb):
            return False

    class Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["cwd"] = kwargs["cwd"]
        calls["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr(ci_entry.tempfile, "TemporaryDirectory", FakeTempDir)
    monkeypatch.setattr(ci_entry.subprocess, "run", fake_run)
    monkeypatch.setattr(ci_entry, "clean_env", lambda: {"PYTHONIOENCODING": "utf-8"})

    assert ci_entry.run_ci_pytest_entrypoint(
        ["tests/test_example.py"],
        root=root,
    ) == 0
    assert calls["prefix"].startswith("engram-ci-pytest-")
    assert calls["cwd"] == str(outside)
    assert str(target.resolve()) in calls["cmd"]
    assert calls["env"] == {"PYTHONIOENCODING": "utf-8"}
