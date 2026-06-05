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


def test_default_targets_cover_script_import_regressions(ci_entry):
    assert ci_entry.DEFAULT_TARGETS == (
        "tests/test_admission_guard.py",
        "tests/test_memory_eval_suite.py",
        "tests/test_recall_eval.py",
        "tests/test_cross_tool_resume_benchmark.py",
    )


def test_discover_script_import_tests_finds_direct_scripts_imports(ci_entry, tmp_path):
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_direct.py").write_text(
        "from scripts.check_admission import evaluate_fixture\n",
        encoding="utf-8",
    )
    (tests / "test_plain.py").write_text("def test_noop(): pass\n", encoding="utf-8")

    assert ci_entry.discover_script_import_tests(root) == ("tests/test_direct.py",)


def test_default_targets_can_include_discovered_script_imports(ci_entry, tmp_path):
    root = tmp_path / "repo"
    tests = root / "tests"
    tests.mkdir(parents=True)
    (tests / "test_extra.py").write_text(
        "import scripts.run_memory_evals\n",
        encoding="utf-8",
    )

    targets = ci_entry.default_targets(root, discover_script_imports=True)

    assert "tests/test_admission_guard.py" in targets
    assert "tests/test_extra.py" in targets


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


def test_clean_env_replaces_pythonpath_with_repo_src(ci_entry, tmp_path):
    root = tmp_path / "repo"
    env = ci_entry.clean_env(root, {"PYTHONPATH": "old", "OTHER": "1"})

    assert env["PYTHONPATH"] == str((root / "src").resolve())
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
    monkeypatch.setattr(
        ci_entry,
        "clean_env",
        lambda root_arg: {"PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(root_arg / "src")},
    )

    assert ci_entry.run_ci_pytest_entrypoint(
        ["tests/test_example.py"],
        root=root,
    ) == 0
    assert calls["prefix"].startswith("engram-ci-pytest-")
    assert calls["cwd"] == str(outside)
    assert str(target.resolve()) in calls["cmd"]
    assert calls["env"] == {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(root / "src"),
    }
