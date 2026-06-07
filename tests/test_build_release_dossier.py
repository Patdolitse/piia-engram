"""Tests for the local release dossier builder."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_release_dossier.py"


def _load():
    spec = importlib.util.spec_from_file_location("_build_release_dossier", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _repo(tmp_path: Path) -> Path:
    root = tmp_path
    (root / "docs").mkdir()
    (root / "scripts").mkdir()
    (root / "docs" / "public-facts.json").write_text(
        json.dumps({
            "facts": {
                "test_passed": 12,
                "test_skipped": 1,
                "test_collected": 13,
            }
        }),
        encoding="utf-8",
    )
    return root


def test_build_dossier_is_local_and_read_only(monkeypatch, tmp_path):
    mod = _load()
    root = _repo(tmp_path)

    def fake_run(cmd, root_path, timeout=120):
        joined = " ".join(cmd)
        assert "push" not in joined
        assert "release" not in joined
        assert "deploy" not in joined
        if cmd[:2] == ["git", "tag"]:
            return 0, "v1.0.0", ""
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return 0, "abc1234", ""
        if cmd[:2] == ["git", "log"]:
            return 0, "abc1234 feat: local work", ""
        if cmd[-1] == "--json":
            return 0, '{"total": 87, "core": 17, "advanced": 70}', ""
        return 1, "", "unexpected"

    monkeypatch.setattr(mod, "_run", fake_run)
    dossier = mod.build_dossier(root)

    assert dossier["read_only"] is True
    assert dossier["public_actions_performed"] == []
    assert dossier["git"]["head"] == "abc1234"
    assert dossier["mcp_tool_counts"] == {"total": 87, "core": 17, "advanced": 70}
    assert "owner confirmation" in dossier["publication_boundary"]


def test_render_markdown_keeps_public_actions_blocked(tmp_path):
    mod = _load()
    dossier = {
        "generated_at": "2026-06-06",
        "git": {
            "head": "abc1234",
            "latest_tag": "v1.0.0",
            "local_commits_after_latest_tag": ["abc1234 feat: local work"],
        },
        "public_facts": {"facts": {"test_passed": 12, "test_skipped": 1, "test_collected": 13}},
        "mcp_tool_counts": {"total": 87, "core": 17, "advanced": 70},
    }

    text = mod.render_markdown(dossier)

    assert "Local planning artifact only" in text
    assert "MCP tools: 87 total / 17 core / 70 advanced" in text
    assert "git push" in text
    assert "MCP Registry publish" in text
