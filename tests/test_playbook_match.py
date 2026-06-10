"""Unit tests for playbook_match — cold-start "playbook finds you" matching.

Covers the pure matching/rendering module only; the MCP wiring
(get_user_context surfacing) is covered in test_mcp_tools.py.
"""

from __future__ import annotations

from piia_engram.playbook_match import (
    match_playbooks,
    render_matched_section,
)


def _pb(title="MCP Registry 发布流程", triggers=None, **extra):
    pb = {
        "id": "pb-1",
        "title": title,
        "triggers": triggers if triggers is not None else ["发布", "registry"],
        "steps": [{"order": 1, "action": "build"}, {"order": 2, "action": "publish"}],
        "status": "active",
    }
    pb.update(extra)
    return pb


# ---------------------------------------------------------------------------
# Trigger matching semantics
# ---------------------------------------------------------------------------


class TestTriggerMatching:
    def test_cjk_trigger_matches_as_substring(self):
        matches = match_playbooks("帮我把新版本发布到 PyPI", [_pb()])
        assert len(matches) == 1
        assert "发布" in matches[0]["matched_triggers"]

    def test_ascii_trigger_matches_word_bounded(self):
        matches = match_playbooks("push this to the registry please", [_pb()])
        assert len(matches) == 1
        assert "registry" in matches[0]["matched_triggers"]

    def test_ascii_trigger_does_not_match_inside_word(self):
        # "git" must not match "digital" — word boundary required for ASCII.
        pb = _pb(title="Git workflow", triggers=["git"])
        assert match_playbooks("the digital marketing plan", [pb]) == []

    def test_ascii_trigger_is_case_insensitive(self):
        pb = _pb(title="Registry publish", triggers=["Registry"])
        matches = match_playbooks("update the REGISTRY entry", [pb])
        assert len(matches) == 1

    def test_no_trigger_hit_means_no_match_even_if_title_similar(self):
        # Precision anchor: title-only similarity must NOT surface a playbook.
        pb = _pb(title="发布 registry 指南", triggers=["完全不相关"])
        assert match_playbooks("怎么发布到 registry", [pb]) == []

    def test_single_char_trigger_is_ignored(self):
        pb = _pb(triggers=["发"])
        assert match_playbooks("发布新版本", [pb]) == []

    def test_empty_prompt_returns_nothing(self):
        assert match_playbooks("", [_pb()]) == []
        assert match_playbooks("   ", [_pb()]) == []


# ---------------------------------------------------------------------------
# Ranking, limits, hygiene
# ---------------------------------------------------------------------------


class TestRankingAndHygiene:
    def test_more_trigger_hits_rank_higher(self):
        one_hit = _pb(title="A", triggers=["发布"], id="pb-one")
        two_hits = _pb(title="B", triggers=["发布", "registry"], id="pb-two")
        matches = match_playbooks("发布到 registry", [one_hit, two_hits])
        assert [m["playbook_id"] for m in matches] == ["pb-two", "pb-one"]

    def test_limit_is_respected(self):
        pbs = [_pb(id=f"pb-{i}", title=f"流程{i}") for i in range(5)]
        matches = match_playbooks("发布新版本", pbs, limit=2)
        assert len(matches) == 2

    def test_tie_broken_by_last_reviewed_desc(self):
        older = _pb(id="pb-old", title="A", last_reviewed="2026-01-01T00:00:00")
        newer = _pb(id="pb-new", title="B", last_reviewed="2026-06-01T00:00:00")
        matches = match_playbooks("发布到 registry", [older, newer], limit=1)
        assert matches[0]["playbook_id"] == "pb-new"

    def test_undecrypted_ciphertext_title_is_skipped(self):
        pb = _pb(title="enc:v1:gcm:deadbeef")
        assert match_playbooks("发布新版本", [pb]) == []

    def test_undecrypted_trigger_never_matches(self):
        pb = _pb(triggers=["enc:v1:gcm:deadbeef"])
        assert match_playbooks("enc:v1:gcm:deadbeef 发布", [pb]) == []

    def test_title_newlines_collapsed_no_heading_spoofing(self):
        pb = _pb(title="发布流程\n## 假冒小节\n继续")
        matches = match_playbooks("发布新版本", [pb])
        assert len(matches) == 1
        assert "\n" not in matches[0]["title"]

    def test_non_dict_candidates_are_ignored(self):
        assert match_playbooks("发布", ["junk", None, 42]) == []


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------


class TestRenderSection:
    def test_empty_matches_render_empty_string(self):
        assert render_matched_section([]) == ""

    def test_zh_section_contains_pointer_and_passive_reference_hint(self):
        matches = match_playbooks("发布到 registry", [_pb()])
        section = render_matched_section(matches, lang="zh")
        assert "## 相关 Playbook" in section
        assert "get_playbook" in section
        assert "pb-1" in section
        assert "被动参考" in section

    def test_en_section_contains_pointer_and_passive_reference_hint(self):
        matches = match_playbooks("publish to the registry", [_pb()])
        section = render_matched_section(matches, lang="en")
        assert "## Matched Playbooks" in section
        assert "get_playbook" in section
        assert "pb-1" in section
        assert "passive references" in section

    def test_section_lists_matched_triggers_and_steps_count(self):
        matches = match_playbooks("发布到 registry", [_pb()])
        section = render_matched_section(matches, lang="zh")
        assert "发布" in section
        assert "2 步" in section
