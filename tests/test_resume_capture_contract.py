"""Synthetic budget contracts: the text and optional packs agree on key fields."""
import json

import pytest

from piia_engram.core import Engram


SCENARIOS = ("ordinary", "long_background", "failure", "conflict", "missing_next",
             "stale_anchor", "mixed_language", "same_name", "pending_verified", "history")


@pytest.mark.parametrize("budget", [512, 2000])
def test_selected_key_lists_survive_markdown_and_agent_output(tmp_path, budget):
    eng = Engram(root=tmp_path / "store")
    project = tmp_path / "sample"
    project.mkdir()
    state = {"current_focus": "Validate the output.",
             "next_actions": [f"Check sample component {i}." for i in range(8)],
             "blocked_on": [f"Blocked by sample check {i}." for i in range(8)],
             "last_completed": [f"Completed sample check {i}." for i in range(8)],
             "constraints": ["Preserve API compatibility.", "Keep writes isolated."]}
    eng.save_project_snapshot(str(project), {"current_state": state})
    resume = eng.build_project_resume_pack(str(project), token_budget=budget)
    agent = eng.build_agent_context_pack(str(project), token_budget=budget)
    brief = eng.get_resume_brief(str(project), token_budget=budget)
    for key in ("next_actions", "blocked_on", "last_completed"):
        assert resume["handoff"][key] == state[key]
        for value in resume["handoff"][key]:
            assert value in brief["markdown"]
    for value in state["constraints"]:
        assert value in brief["markdown"]
        assert value in agent["constraints"]
    for key in ("next_actions", "blocked_on"):
        assert agent["focus"][key] == resume["handoff"][key]


@pytest.mark.parametrize("field,label", [("next_actions", "Next"),
    ("last_completed", "Completed"), ("blocked_on", "Risk")])
def test_digest_key_truncation_is_visible_in_all_resume_outputs(tmp_path, field, label):
    eng = Engram(root=tmp_path / "store")
    project = tmp_path / "sample"
    project.mkdir()
    original = "Blocked: " + "validate sample output " * 13 + "before release."
    eng.save_agent_context("test", f"{label}: {original}\n", project_folder=str(project))
    resume = eng.build_project_resume_pack(str(project), token_budget=512)
    selected = resume["handoff"][field][0]
    assert selected != original and selected.endswith("…")
    assert any(item["kind"] == field for item in resume["omitted"])
    assert "get_session_digest" in resume["pack_meta"]["retrieval_hint"]
    brief = eng.get_resume_brief(str(project), token_budget=512)
    assert field in brief["omitted"]["sections"]
    agent = eng.build_agent_context_pack(str(project), token_budget=512)
    assert agent["pack_meta"]["counts"]["omitted"] > 0
    if field != "last_completed":
        assert agent["focus"][field] == resume["handoff"][field]


def test_public_copy_scopes_review_to_durable_memory():
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1]
    for filename in ("CHANGELOG.md", "CHANGELOG.zh-CN.md"):
        text = (root / filename).read_text(encoding="utf-8")
        assert not re.search(r"Codex-implements|Claude-accepts|Claude acceptance|Codex subagent|Claude 验收|"
                             r"Claude Code 只读|Codex (?:实现|记录|独立|审计)|独立（Codex）", text)
    for filename in ("docs/trust.md", "docs/user-guide.md", "README.md"):
        text = (root / filename).read_text(encoding="utf-8")
        assert "same approved context" not in text
        assert "earlier session records" in text
        assert "not reviewed" in text
    for filename in ("docs/user-guide.zh-CN.md", "README.zh-CN.md"):
        text = (root / filename).read_text(encoding="utf-8")
        assert "已认可的上下文" not in text and "已确认上下文" not in text
        assert "先前会话记录" in text and "未经审核" in text


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
    if scenario == "failure":
        state["blocked_on"] = []
        state["latest_failure"] = blocker
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
    if scenario == "missing_next":
        assert resume["handoff"]["next_actions"] == []
        assert "- **next_action**: unknown" in md
    assert constraint in json.dumps(resume, ensure_ascii=False)
    assert constraint in agent["constraints"]
    for text in forbidden:
        # Supporting earlier records may contain a historical action; the
        # current handoff must not promote it over the chosen checkpoint.
        assert text not in md.split("## Recent session contexts", 1)[0]
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
    source = eng.get_project_snapshot(str(project))
    assert source["current_state"]["next_actions"] == ["action " * 400]
    assert source["current_state"]["constraints"] == ["constraint " * 400]


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


@pytest.mark.parametrize("budget", [128, 256, 512, 2000])
def test_direct_pack_budget_contract_matches_brief(tmp_path, budget):
    eng = Engram(root=tmp_path / "store")
    project = tmp_path / "sample"
    project.mkdir()
    eng.save_project_snapshot(str(project), {"current_state": {
        "current_focus": "Validate sample.", "next_actions": ["Run focused validation."],
        "blocked_on": ["Blocked: prior check failed."], "constraints": ["Keep input format."]}})
    resume = eng.build_project_resume_pack(str(project), token_budget=budget)
    agent = eng.build_agent_context_pack(str(project), token_budget=budget)
    brief = eng.get_resume_brief(str(project), token_budget=budget)
    assert resume["handoff"]["next_actions"] == agent["focus"]["next_actions"]
    assert resume["handoff"]["blocked_on"] == agent["focus"]["blocked_on"]
    assert "Run focused validation." in brief["markdown"]
    assert "Blocked: prior check failed." in brief["markdown"]
    assert "Keep input format." in agent["constraints"]
