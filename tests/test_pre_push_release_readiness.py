"""Tests for the local pre-push / pre-release readiness aggregator."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_pre_push_release_readiness.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_pre_push_release_readiness", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_mode_does_not_run_full_pytest(monkeypatch, tmp_path):
    mod = _load()
    calls = []

    def fake_run(cmd, root, timeout):
        calls.append(cmd)
        return 0, "ok", ""

    monkeypatch.setattr(mod, "_run", fake_run)
    report = mod.run_checks(tmp_path)

    assert report["ok"] is True
    assert report["full_tests"] is False
    assert not any("-m" in cmd and "pytest" in cmd for cmd in calls)
    assert any(
        any(str(part).endswith("check_public_release_surface.py") for part in cmd)
        for cmd in calls
    )


def test_full_tests_mode_adds_pytest(monkeypatch, tmp_path):
    mod = _load()
    calls = []

    def fake_run(cmd, root, timeout):
        calls.append(cmd)
        return 0, "ok", ""

    monkeypatch.setattr(mod, "_run", fake_run)
    report = mod.run_checks(tmp_path, full_tests=True)

    assert report["ok"] is True
    assert report["full_tests"] is True
    assert any("-m" in cmd and "pytest" in cmd for cmd in calls)


def test_failed_check_marks_report_not_ok(monkeypatch, tmp_path):
    mod = _load()

    def fake_run(cmd, root, timeout):
        if any(str(part).endswith("check_public_claim_drift.py") for part in cmd):
            return 1, "", "drift"
        return 0, "ok", ""

    monkeypatch.setattr(mod, "_run", fake_run)
    report = mod.run_checks(tmp_path)

    assert report["ok"] is False
    failed = [item for item in report["results"] if not item["ok"]]
    assert len(failed) == 1
    assert failed[0]["name"] == "public_claim_drift"


def test_report_note_states_read_only(monkeypatch, tmp_path):
    mod = _load()

    def fake_run(cmd, root, timeout):
        return 0, "ok", ""

    monkeypatch.setattr(mod, "_run", fake_run)
    report = mod.run_checks(tmp_path)

    assert "read-only local checks only" in report["note"]
    assert "no public action performed" in report["note"]


def test_render_text_states_no_public_action(tmp_path):
    mod = _load()
    report = {
        "ok": True,
        "root": str(tmp_path),
        "full_tests": False,
        "results": [{"name": "x", "ok": True, "stdout": "ok\nmore", "stderr": ""}],
    }
    text = mod.render_text(report)

    assert "[OK] x" in text
    assert "No push/tag/release/upload/registry/deploy/external refresh was performed." in text


def test_render_text_failed_check_prefers_stderr_tail(tmp_path):
    mod = _load()
    report = {
        "ok": False,
        "root": str(tmp_path),
        "full_tests": False,
        "results": [{
            "name": "x",
            "ok": False,
            "stdout": "benign progress",
            "stderr": "first\nsecond\nthird\nactual failure",
        }],
    }
    text = mod.render_text(report)

    assert "[FAIL] x" in text
    assert "benign progress" not in text
    assert "actual failure" in text
    assert "first" not in text


def test_main_returns_nonzero_when_report_fails(monkeypatch, tmp_path, capsys):
    mod = _load()

    def fake_run_checks(root, *, full_tests=False, timeout=1200):
        return {
            "ok": False,
            "root": str(tmp_path),
            "full_tests": full_tests,
            "results": [{"name": "x", "ok": False, "stdout": "", "stderr": "nope"}],
            "note": "read-only local checks only; no public action performed",
        }

    monkeypatch.setattr(mod, "run_checks", fake_run_checks)

    assert mod.main(["--root", str(tmp_path)]) == 1
    assert "[FAIL] x" in capsys.readouterr().out


# --- anti-drift: the CI-mirror list must match ci.yml's `test` job ------------


def _ci_test_job_run_commands():
    """Parse .github/workflows/ci.yml and return the `test` job's single-line
    ``run:`` commands, excluding the dependency install.

    Test-time YAML parsing only. The runtime gate deliberately does NOT parse
    ci.yml (CI YAML is an orchestration format, not a stable runner API); this
    test is what keeps CI_TEST_JOB_CHECKS honest.
    """
    import re

    ci_path = (
        Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
    )
    lines = ci_path.read_text(encoding="utf-8").splitlines()
    start = end = None
    for i, line in enumerate(lines):
        if re.match(r"^  test:\s*$", line):
            start = i
            continue
        if start is not None and re.match(r"^  \w[\w-]*:\s*$", line):
            end = i
            break
    assert start is not None, "could not find `test:` job in ci.yml"
    if end is None:
        end = len(lines)

    commands = []
    for line in lines[start:end]:
        m = re.match(r"^\s*run:\s*(\S.*?)\s*$", line)
        if m:
            cmd = m.group(1)
            # Exact-exclude only the dependency install step. Any OTHER non-guard
            # run step added to ci.yml's test job should TRIP this test so it gets
            # reconciled deliberately, rather than being silently swallowed.
            if cmd == 'pip install -e ".[dev]"':
                continue
            commands.append(cmd)
    return commands


def _normalize(tokens):
    """Drop the interpreter and collapse ``-m pytest`` so ci.yml's ``python`` /
    ``pytest`` commands and the script's ``sys.executable`` / ``-m pytest`` forms
    compare equal."""
    import os

    parts = list(tokens)
    if parts and (
        parts[0] == "python"
        or os.path.basename(str(parts[0])).lower().startswith("python")
    ):
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] == "-m" and parts[1] == "pytest":
        parts = ["pytest"] + parts[2:]
    return tuple(parts)


def test_ci_test_job_parity():
    """CI_TEST_JOB_CHECKS + the pytest step must mirror ci.yml's `test` job
    exactly, in order. If a guard is added/removed/reordered in ci.yml without
    updating the readiness mirror, this fails -- which is the whole point: it
    stops the pre-push gate from silently falling behind CI (the root cause of
    the 4.3.0 and 4.4.0 CI-reds)."""
    import shlex

    mod = _load()
    expected = [_normalize(shlex.split(cmd)) for cmd in _ci_test_job_run_commands()]
    actual = [_normalize(cmd) for _name, cmd in mod.CI_TEST_JOB_CHECKS]
    actual.append(_normalize(mod.FULL_TEST_CHECK[1]))

    assert actual == expected, (
        "check_pre_push_release_readiness CI mirror drifted from ci.yml `test` job.\n"
        f"  ci.yml   : {expected}\n"
        f"  readiness: {actual}"
    )
