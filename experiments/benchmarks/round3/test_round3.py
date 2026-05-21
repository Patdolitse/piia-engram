from __future__ import annotations

import json


def test_round3_scenarios_have_65_items_and_variant_metadata():
    from experiments.benchmarks.round3.scenarios_v3 import SCENARIOS_V3, validate_scenarios_v3

    validate_scenarios_v3(SCENARIOS_V3)

    assert len(SCENARIOS_V3) == 65
    variants = [scenario for scenario in SCENARIOS_V3 if scenario["test_group"] == "F"]
    assert len(variants) == 15
    assert all(scenario["variant_of"] for scenario in variants)
    assert all(scenario["original_keywords"] for scenario in variants)

    synonym_groups = {
        scenario["synonym_group"]
        for scenario in SCENARIOS_V3
        if scenario.get("synonym_group")
    }
    assert {
        "lesson_lockfile",
        "decision_pytest",
        "lesson_incident_variant",
        "decision_stack_variant",
        "recall_auth_variant",
    } <= synonym_groups


def test_domain_aliases_accept_common_equivalents():
    from experiments.benchmarks.round3.domain_aliases import domain_matches

    assert domain_matches("python", "CPython")
    assert domain_matches("database", "postgres")
    assert domain_matches("testing", "pytest")
    assert domain_matches("project_context", "project_context")
    assert not domain_matches("frontend", "backend")


def test_prompts_do_not_include_expected_labels_or_few_shot_examples():
    from experiments.benchmarks.round3.llm_judge import (
        build_atomic_prompt,
        build_coordinator_prompt,
        load_atomic_tools_desc,
        load_coordinator_tools_desc,
    )

    scenario = {
        "user_input": "记录一条经验：Windows 编码会影响上传脚本",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "python",
    }

    atomic_prompt = build_atomic_prompt(scenario["user_input"], load_atomic_tools_desc())
    coordinator_prompt = build_coordinator_prompt(
        scenario["user_input"],
        load_coordinator_tools_desc(),
    )

    for prompt in (atomic_prompt, coordinator_prompt):
        assert "expected_" not in prompt
        assert "few-shot" not in prompt.lower()
        assert "示例" not in prompt
        assert scenario["user_input"] in prompt

    assert "expected_atomic_tool" not in atomic_prompt
    assert "expected_kind" not in coordinator_prompt


def test_majority_vote_uses_three_calls_and_writes_raw_jsonl(tmp_path):
    from experiments.benchmarks.round3.llm_judge import LLMJudge

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages, max_tokens=300):
            self.calls += 1
            payload = {"tool": "remember", "kind": "lesson", "domain": "python", "reasoning": "x"}
            return {
                "content": json.dumps(payload),
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "model": "fake",
            }

    raw_path = tmp_path / "raw.jsonl"
    judge = LLMJudge(client=FakeClient(), raw_log_path=raw_path, runs_per_scenario=3)
    result = judge.judge_coordinator("T01", "记录一条 Python 经验")

    assert result["tool"] == "remember"
    assert result["kind"] == "lesson"
    assert result["domain"] == "python"
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert all(json.loads(line)["scenario_id"] == "T01" for line in lines)


def test_round3_runner_writes_report_with_fake_judge(tmp_path):
    from experiments.benchmarks.round3.run_round3 import run_round3

    class FakeJudge:
        def __init__(self) -> None:
            self.total_calls = 0
            self.failed_calls = 0
            self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        def judge_atomic(self, scenario_id, user_input):
            self.total_calls += 3
            if "相似" in user_input:
                return {"tool": "find_similar_knowledge", "reasoning": "similar"}
            if "项目快照" in user_input or "保存当前项目" in user_input:
                return {"tool": "save_project_snapshot", "reasoning": "snapshot"}
            return {"tool": "search_knowledge", "reasoning": "default"}

        def judge_coordinator(self, scenario_id, user_input):
            self.total_calls += 3
            return {"tool": "remember", "kind": "lesson", "domain": "python", "reasoning": "default"}

    result = run_round3(output_dir=tmp_path, judge=FakeJudge())

    assert result["scenario_count"] == 65
    assert (tmp_path / "results_d.json").exists()
    assert (tmp_path / "results_c.json").exists()
    assert (tmp_path / "results_e.json").exists()
    assert (tmp_path / "results_f.json").exists()
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "测试 F 结果" in report
    assert "综合决策" in report
