from __future__ import annotations

import json


def test_round4_scenario_inventory_is_fixed_and_reviewable():
    from experiments.benchmarks.round4_regression.scenarios_r1 import (
        ONBOARDING_SCENARIOS,
        validate_onboarding_scenarios,
    )
    from experiments.benchmarks.round4_regression.scenarios_r3 import (
        EXTRACTION_SCENARIOS,
        validate_extraction_scenarios,
    )

    validate_onboarding_scenarios(ONBOARDING_SCENARIOS)
    validate_extraction_scenarios(EXTRACTION_SCENARIOS)

    assert len(ONBOARDING_SCENARIOS) == 5
    assert {item["language_group"] for item in ONBOARDING_SCENARIOS} == {"zh", "en", "multi"}
    assert len(EXTRACTION_SCENARIOS) == 15
    assert [item["category"] for item in EXTRACTION_SCENARIOS].count("lesson") == 5
    assert [item["category"] for item in EXTRACTION_SCENARIOS].count("decision") == 5
    assert [item["category"] for item in EXTRACTION_SCENARIOS].count("ordinary") == 5


def test_round4_judge_prompts_hide_r3_expected_category_and_write_raw(tmp_path):
    from experiments.benchmarks.round4_regression.llm_judge import LLMJudge
    from experiments.benchmarks.round4_regression.scenarios_r3 import EXTRACTION_SCENARIOS

    scenario = EXTRACTION_SCENARIOS[0]
    prompt = LLMJudge.build_extraction_prompt_for_test(
        scenario_id=scenario["id"],
        dialogue=scenario["dialogue"],
        extraction={"saved_lessons": 1, "saved_decisions": 0, "results": []},
    )

    assert "expected" not in prompt.lower()
    assert "expected_category" not in prompt.lower()
    assert f'"category": "{scenario["category"]}"' not in prompt

    class FakeClient:
        model = "fake-deepseek"

        def complete(self, messages, max_tokens=500):
            return {
                "content": json.dumps(
                    {
                        "should_extract": True,
                        "extracted_relevant": True,
                        "false_positive": False,
                        "semantic_accuracy": 0.95,
                        "reasoning": "The extraction matches the durable point.",
                    }
                ),
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "model": self.model,
            }

    raw_path = tmp_path / "raw.jsonl"
    judge = LLMJudge(client=FakeClient(), raw_log_path=raw_path, runs_per_scenario=3)
    result = judge.judge_extraction("R3-L01", scenario["dialogue"], {"saved_lessons": 1})

    assert result["should_extract"] is True
    assert result["extracted_relevant"] is True
    assert judge.total_calls == 3
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["scenario_id"] == "R3-L01"
    assert first["messages"]
    assert first["response"]
    assert first["raw_content"]


def test_round4_onboarding_seed_uses_isolated_engram(tmp_path):
    from experiments.benchmarks.round4_regression.scenarios_r1 import ONBOARDING_SCENARIOS
    from experiments.benchmarks.round4_regression.test_r1_onboarding import seed_onboarding_profile
    from piia_engram.core import Engram

    engram = Engram(root=tmp_path / "engram")
    seed = seed_onboarding_profile(engram, ONBOARDING_SCENARIOS[0])

    context = engram.generate_context()
    search = engram.search_knowledge(seed["primary_search_query"], scope="lessons", limit=5)

    assert seed["profile"]["role"]
    assert seed["profile"]["tech_stack"]
    assert seed["profile"]["role"] in context
    assert search["lessons"]
    assert (tmp_path / "engram" / "knowledge" / "lessons.json").exists()


def test_round4_runner_writes_results_and_report_with_fake_judge(tmp_path):
    from experiments.benchmarks.round4_regression.run_round4 import run_round4

    class FakeJudge:
        runs_per_scenario = 3
        total_calls = 0
        failed_calls = 0
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        client = type("Client", (), {"model": "fake-deepseek"})()

        def judge_onboarding(self, scenario_id, profile_reference, context, search_results):
            self.total_calls += 3
            return {
                "role_readable": True,
                "tech_stack_readable": True,
                "lesson_present_in_context": True,
                "lesson_searchable": True,
                "language_ok": True,
                "reasoning": "ok",
            }

        def judge_context_snapshot(self, scenario_id, context, profile_reference, key_knowledge_reference):
            self.total_calls += 3
            return {
                "identity_complete": True,
                "key_lesson_mentioned": True,
                "key_decision_mentioned": True,
                "key_knowledge_mentioned": True,
                "reasoning": "ok",
            }

        def judge_similarity(self, scenario_id, context_a, context_b):
            self.total_calls += 3
            return {"similarity": 0.99, "reasoning": "near identical"}

        def judge_extraction(self, scenario_id, dialogue, extraction):
            self.total_calls += 3
            return {
                "should_extract": True,
                "extracted_relevant": True,
                "false_positive": False,
                "semantic_accuracy": 0.95,
                "reasoning": "ok",
            }

    result = run_round4(output_dir=tmp_path, judge=FakeJudge())

    assert result["r1"]["scenario_count"] == 5
    assert result["r2"]["call_count"] == 10
    assert result["r3"]["scenario_count"] == 15
    assert (tmp_path / "results_r1.json").exists()
    assert (tmp_path / "results_r2.json").exists()
    assert (tmp_path / "results_r3.json").exists()
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "第四轮回归基准报告" in report
    assert "regression_baseline:" in report
