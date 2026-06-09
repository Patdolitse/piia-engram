"""Tests for the post-push closeout dry-run helper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "post_push_closeout.py"


def _load():
    spec = importlib.util.spec_from_file_location("_post_push_closeout", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "engram"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "pyproject.toml").write_text('version = "1.2.3"\n', encoding="utf-8")
    (root / "docs" / "public-facts.json").write_text(
        json.dumps({"facts": {"test_collected": 42}}),
        encoding="utf-8",
    )
    return root


def test_closeout_status_is_dry_run_and_does_not_query_by_default(monkeypatch, tmp_path):
    mod = _load()
    root = _repo(tmp_path)
    calls = []

    def fake_run(cmd, root_path, timeout=120):
        calls.append(cmd)
        if cmd[:2] == ["git", "tag"]:
            return 0, "v1.2.0\nv1.1.0", ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(mod, "_run", fake_run)
    status = mod.collect_closeout_status(root)

    assert status["dry_run"] is True
    assert status["auto_status"]["version"] == "1.2.3"
    assert status["auto_status"]["tests"] == "42 collected"
    assert status["auto_status"]["github_stars"] == "not-queried"
    assert not any("push" in part for cmd in calls for part in cmd)


def _fake_gh(workflow_runs, *, latest_release='[{"tagName":"v3.53.0","name":"x","isLatest":true,"publishedAt":"2026-06-08"}]'):
    """Build a fake _run that answers git rev-parse + gh run/release list."""
    def fake_run(cmd, root_path, timeout=120):
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "abc1234def5678", ""
        if cmd[:3] == ["gh", "run", "list"]:
            return 0, json.dumps(workflow_runs), ""
        if cmd[:3] == ["gh", "release", "list"]:
            return 0, latest_release, ""
        raise AssertionError(f"unexpected command: {cmd}")
    return fake_run


def test_github_status_detects_no_release_on_plain_push(monkeypatch, tmp_path):
    mod = _load()
    root = _repo(tmp_path)
    runs = [
        {"name": "CI", "event": "push", "status": "completed", "conclusion": "success"},
        {"name": "Guard strategic files", "event": "push", "status": "completed", "conclusion": "success"},
    ]
    monkeypatch.setattr(mod, "_run", _fake_gh(runs))
    monkeypatch.setattr(mod, "_fetch_pypi_version", lambda package="piia-engram", timeout=15: "3.53.0")

    status = mod.collect_github_status(root)

    assert status["read_only"] is True
    assert status["release_triggered"] is False
    assert status["release_runs"] == []
    assert status["ci_failed"] == []
    assert status["ci_in_progress"] == []
    assert status["pypi_version"] == "3.53.0"
    text = mod.render_github_status(status)
    assert "Release triggered by this push: NO" in text
    assert "All checks: green" in text


def test_github_status_flags_accidental_release(monkeypatch, tmp_path):
    mod = _load()
    root = _repo(tmp_path)
    runs = [
        {"name": "CI", "event": "push", "status": "completed", "conclusion": "success"},
        {"name": "Publish to PyPI", "event": "release", "status": "completed", "conclusion": "success"},
    ]
    monkeypatch.setattr(mod, "_run", _fake_gh(runs))
    monkeypatch.setattr(mod, "_fetch_pypi_version", lambda package="piia-engram", timeout=15: "3.54.0")

    status = mod.collect_github_status(root)

    assert status["release_triggered"] is True
    assert [r["name"] for r in status["release_runs"]] == ["Publish to PyPI"]
    text = mod.render_github_status(status)
    assert "RELEASE TRIGGERED BY THIS PUSH: yes" in text


def test_github_status_reports_in_progress_checks(monkeypatch, tmp_path):
    mod = _load()
    root = _repo(tmp_path)
    runs = [
        {"name": "CI", "event": "push", "status": "in_progress", "conclusion": ""},
    ]
    monkeypatch.setattr(mod, "_run", _fake_gh(runs))
    monkeypatch.setattr(mod, "_fetch_pypi_version", lambda package="piia-engram", timeout=15: "3.53.0")

    status = mod.collect_github_status(root, query_pypi=False) if False else mod.collect_github_status(root)

    assert status["ci_in_progress"] and status["ci_in_progress"][0]["name"] == "CI"
    assert status["release_triggered"] is False
    text = mod.render_github_status(status)
    assert "Still running: CI" in text


def test_render_text_states_no_file_writes(tmp_path):
    mod = _load()
    text = mod.render_text({
        "project_registry": str(tmp_path / "PROJECT_REGISTRY.md"),
        "auto_status": {
            "version": "1.2.3",
            "latest_tag": "v1.2.0",
            "tests": "42 collected",
            "github_stars": "not-queried",
            "last_updated": "2026-06-06",
        },
    })

    assert "No files were written" in text
    assert "no public actions" in text
