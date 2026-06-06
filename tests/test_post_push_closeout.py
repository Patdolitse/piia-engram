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
