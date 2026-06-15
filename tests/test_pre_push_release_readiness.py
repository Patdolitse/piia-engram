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
