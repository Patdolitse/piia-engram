"""Presence loop — Build 2 (Layer 1): branded resume-brief lead line.

The SessionStart resume brief (injected as model context by the Claude Code /
Cursor hooks, and returned by the get_resume_brief MCP tool) leads with a brand
line so the next AI naturally carries out "[Engram] Resumed N memories …":

    [Engram] Resumed {N} memories from {project} · last session {when}

Honesty rules (same discipline as Build 1's recall header):
  - N counts only memories (lessons + decisions) ACTUALLY included in this brief
    — never an unsubstantiated total.
  - "from {project}" is omitted for an identity-only brief.
  - "· last session {when}" is omitted when there is no recent session to cite
    (never fabricated).
The line is prepended INSIDE the <engram-resume> wrapper (markdown still opens
with the wrapper tag), so existing wrapper/section-order tests stay green.
"""

from __future__ import annotations

from pathlib import Path

from piia_engram.core import Engram
from piia_engram.contexts import _resume_brand_line


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


class TestResumeBrandLine:
    def test_full_line(self):
        assert _resume_brand_line(3, "MyProj", "2026-06-19T19:00:00") == (
            "[Engram] Resumed 3 memories from MyProj · last session 2026-06-19T19:00:00"
        )

    def test_omits_project_when_blank(self):
        assert _resume_brand_line(5, "", "2026-06-19T19:00:00") == (
            "[Engram] Resumed 5 memories · last session 2026-06-19T19:00:00"
        )

    def test_omits_when_blank(self):
        assert _resume_brand_line(2, "Proj", "") == "[Engram] Resumed 2 memories from Proj"

    def test_zero_memories_identity_only(self):
        assert _resume_brand_line(0, "", "") == "[Engram] Resumed 0 memories"


# ---------------------------------------------------------------------------
# Wired into get_resume_brief
# ---------------------------------------------------------------------------


class TestBriefBrandLine:
    def test_brief_leads_with_brand_line_and_counts_surfaced_memories(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        e.update_profile({"role": "developer", "language": "en"}, source_tool="test")
        project = tmp_path / "myproj"
        project.mkdir()
        e.save_project_snapshot(str(project), {"title": "MyProj", "tech_stack": ["python"]})
        e.add_lesson("Use Path.resolve() before hashing", domain="python")
        e.add_lesson("Pin dependencies in CI", domain="python")
        e.add_decision("Database choice", choice="SQLite", reasoning="local-first")

        md = e.get_resume_brief(project_folder=str(project))["markdown"]

        assert "[Engram] Resumed 3 memories from MyProj" in md
        # The brand line must LEAD the content (before the handoff hero) and stay
        # inside the wrapper.
        assert md.startswith('<engram-resume priority="high">\n')
        assert md.index("[Engram] Resumed") < md.index("## 30-second handoff")

    def test_identity_only_brief_omits_project(self, tmp_path: Path):
        e = Engram(root=tmp_path)
        e.update_profile({"role": "developer", "language": "en"}, source_tool="test")
        md = e.get_resume_brief()["markdown"]
        assert "[Engram] Resumed 0 memories" in md
        assert "[Engram] Resumed 0 memories from" not in md  # no project clause
