"""Task 3: save_agent_context attaches a session digest as adjacent metadata.

The digest is stored as a sidecar (`{session_id}.digest.json`), backward-
compatible: legacy sessions without a digest still load, and read-only resume
paths never mutate stored records just to backfill one.
"""

from __future__ import annotations

import json

from piia_engram.core import Engram

# Assembled at runtime so static secret scanners don't flag the test source.
_FAKE_SK_KEY = "sk-" + "abcdef1234567890ABCDEF"


def _meaningful_summary() -> str:
    return (
        "Goal: finish the continuity layer.\n"
        "Completed: wrote the digest builder; integrated the sidecar.\n"
        "Next: assemble the resume pack.\n"
    )


class TestSaveAttachesDigest:
    def test_save_writes_digest_for_meaningful_summary(self, tmp_path):
        eng = Engram(root=tmp_path)
        res = eng.save_agent_context(
            "claude_code", _meaningful_summary(), project_folder=str(tmp_path)
        )
        digest = eng.get_session_digest("claude_code", res["session_id"])
        assert digest is not None
        assert digest["schema"] == "session_digest.v1"
        assert "continuity layer" in digest["goal"].lower()

    def test_trivial_summary_writes_no_digest(self, tmp_path):
        eng = Engram(root=tmp_path)
        res = eng.save_agent_context("codex", "ok", project_folder=str(tmp_path))
        assert eng.get_session_digest("codex", res["session_id"]) is None

    def test_digest_redacts_sensitive(self, tmp_path):
        eng = Engram(root=tmp_path)
        summary = f"Goal: ship it.\nCompleted: rotated key {_FAKE_SK_KEY}.\n"
        res = eng.save_agent_context(
            "cursor", summary, project_folder=str(tmp_path)
        )
        digest = eng.get_session_digest("cursor", res["session_id"])
        assert digest is not None
        assert _FAKE_SK_KEY not in json.dumps(digest, ensure_ascii=False)


class TestBackwardCompatibility:
    def test_legacy_session_without_digest_loads(self, tmp_path):
        eng = Engram(root=tmp_path)
        # Simulate a legacy session: a .md body with no digest sidecar.
        tool_dir = eng._contexts_dir / "claude_code"
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "legacy-1.md").write_text(
            "# Session\n\n### 10:00\nold work\n", encoding="utf-8"
        )
        assert eng.get_session_digest("claude_code", "legacy-1") is None
        # Recent-context read still works over a legacy session.
        recent = eng.get_recent_context(tool="claude_code", limit=1)
        assert recent and "old work" in recent[0]["content"]

    def test_resume_read_does_not_create_digest(self, tmp_path):
        eng = Engram(root=tmp_path)
        tool_dir = eng._contexts_dir / "claude_code"
        tool_dir.mkdir(parents=True, exist_ok=True)
        md = tool_dir / "legacy-2.md"
        md.write_text("# Session\n\n### 09:00\nGoal: x\n", encoding="utf-8")
        # A read-only resume assembly must not backfill a digest sidecar.
        eng.get_resume_brief(project_folder=str(tmp_path))
        assert not (tool_dir / "legacy-2.digest.json").exists()
