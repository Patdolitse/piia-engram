"""Synthetic budget contracts: the text and optional packs agree on key fields."""
import json

import pytest

from piia_engram.core import Engram


SCENARIOS = ("ordinary", "long_background", "failure", "conflict", "missing_next",
             "stale_anchor", "mixed_language", "same_name", "pending_verified", "history")


@pytest.mark.parametrize("mode", ["default", "strict"])
@pytest.mark.parametrize("budget", [128, 256, 512, 1500, None])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_key_fields_survive_before_background(tmp_path, monkeypatch, mode, budget, scenario):
    monkeypatch.setenv("ENGRAM_APPROVAL", mode)
    eng = Engram(root=tmp_path / "store")
    project = tmp_path / "sample"
    project.mkdir()
    next_action = "Run focused validation."
    blocker = "Blocked: prior check failed."
    constraint = "Keep the input format."
    if scenario == "mixed_language":
        next_action = "继续验证 sample。"
        blocker = "阻塞：检查失败。"
        constraint = "保留输入格式。"
    state = {"current_focus": "Validate sample.", "last_completed": ["background " * 80],
             "next_actions": [] if scenario == "missing_next" else [next_action],
             "blocked_on": [blocker], "constraints": [constraint]}
    eng.save_project_snapshot(str(project), {"title": "Sample", "current_state": state,
                                            "notes": "background " * 500})
    forbidden = []
    if scenario == "conflict":
        eng.save_agent_context("test", "Completed: earlier result.\nNext: Earlier action.\n",
                               project_folder=str(project))
        eng.save_project_snapshot(str(project), {"current_state": state})
        forbidden.append("Earlier action.")
    if scenario == "stale_anchor":
        eng.save_project_snapshot(str(project), {"current_state": {**state,
            "verified_at": "2001-01-01T00:00:00Z"}})
    if scenario == "same_name":
        other = tmp_path / "other" / "sample"
        other.mkdir(parents=True)
        eng.save_project_snapshot(str(other), {"title": "Sample", "current_state": {
            "next_actions": ["Unrelated action."]}})
        forbidden.append("Unrelated action.")
    if scenario == "pending_verified":
        eng.add_lesson("Pending sample rule.", tier="staging", project_folder=str(project))
        eng.add_lesson("Verified sample rule.", tier="verified", project_folder=str(project))
    if scenario == "history":
        eng.save_project_snapshot(str(project), {"current_state": {
            "next_actions": ["Obsolete action."]}})
        eng.save_project_snapshot(str(project), {"current_state": state})
        forbidden.append("Obsolete action.")
    args = {} if budget is None else {"token_budget": budget}
    brief = eng.get_resume_brief(str(project), include_resume_pack=True,
                                 include_agent_context_pack=True, **args)
    md = brief["markdown"]
    expected_next = "unknown" if scenario == "missing_next" else next_action
    assert expected_next in md
    assert blocker in md
    assert constraint in md
    assert "earlier session record" in md.lower()
    assert "freshness" in md
    assert brief["store"]["id"]
    resume = brief["resume_pack"]
    agent = brief["agent_context_pack"]
    assert resume["handoff"]["next_actions"] == agent["focus"]["next_actions"]
    assert resume["handoff"]["blocked_on"] == agent["focus"]["blocked_on"]
    assert blocker in resume["handoff"]["blocked_on"]
    assert constraint in json.dumps(resume, ensure_ascii=False)
    assert constraint in agent["constraints"]
    for text in forbidden:
        assert text not in md
        assert text not in json.dumps(resume["handoff"], ensure_ascii=False)
    if scenario == "pending_verified":
        assert "Pending sample rule." not in md
        assert "Pending sample rule." not in json.dumps(resume["trusted_context"])
    if budget in (128, 256):
        assert brief["omitted"]["sections"]
        assert "get_project_snapshot" in brief["omitted"]["retrieval_hint"]


def test_long_key_fields_report_field_cuts_without_inventing_completion(tmp_path):
    eng = Engram(root=tmp_path / "store")
    project = tmp_path / "sample"
    project.mkdir()
    eng.save_project_snapshot(str(project), {"current_state": {
        "current_focus": "state " * 400, "next_actions": ["action " * 400],
        "blocked_on": ["failure " * 400], "constraints": ["constraint " * 400]}})
    brief = eng.get_resume_brief(str(project), token_budget=128, include_resume_pack=True)
    assert "next_actions" in brief["omitted"]["sections"]
    assert "blocked_on" in brief["omitted"]["sections"]
    assert "constraints" in brief["omitted"]["sections"]
    assert "…" in brief["resume_pack"]["handoff"]["next_actions"][0]
    assert isinstance(brief["estimated_tokens"], int)


def test_failure_in_digest_is_visible_as_earlier_record(tmp_path):
    eng = Engram(root=tmp_path / "store")
    project = tmp_path / "sample"
    project.mkdir()
    eng.save_agent_context("test", "Goal: validate sample.\nCompleted: " + "background " * 80 +
        "\nNext: Run focused validation.\nTests: failed because input shape differs.\n",
        project_folder=str(project))
    brief = eng.get_resume_brief(str(project), token_budget=256, include_resume_pack=True)
    assert "failed because input shape differs" in brief["markdown"]
    assert "failed because input shape differs" in json.dumps(brief["resume_pack"]["handoff"])


def test_store_identity_distinguishes_same_leaf_roots(tmp_path):
    first = Engram(root=tmp_path / "one" / "store", read_only=True).get_resume_brief()
    second = Engram(root=tmp_path / "two" / "store", read_only=True).get_resume_brief()
    assert first["store"]["id"] != second["store"]["id"]
    assert str(tmp_path) not in json.dumps(first["store"])
