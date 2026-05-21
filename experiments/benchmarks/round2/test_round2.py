from __future__ import annotations

from collections import Counter


def test_round2_dataset_shape_and_grouping():
    from experiments.benchmarks.round2.scenarios_v2 import SCENARIOS_V2, validate_scenarios_v2

    validate_scenarios_v2(SCENARIOS_V2)

    assert len(SCENARIOS_V2) == 50
    assert len({scenario["id"] for scenario in SCENARIOS_V2}) == 50

    groups = Counter(scenario["test_group"] for scenario in SCENARIOS_V2)
    assert groups == {"D": 30, "C": 10, "E": 10}

    synonym_groups = Counter(
        scenario["synonym_group"]
        for scenario in SCENARIOS_V2
        if scenario["test_group"] == "C"
    )
    assert synonym_groups == {"lesson_lockfile": 5, "decision_pytest": 5}


def test_explicit_mode_beats_keyword_classifier_on_remember_scenarios():
    from experiments.benchmarks.round2.explicit_mode import run_explicit_mode_test
    from experiments.benchmarks.round2.scenarios_v2 import SCENARIOS_V2

    result = run_explicit_mode_test(SCENARIOS_V2)

    assert result["pass"] is True
    assert result["explicit"]["hard_kind_accuracy"] >= 0.95
    assert result["explicit"]["domain_accuracy"] >= 0.90
    assert result["keyword"]["domain_accuracy"] < result["explicit"]["domain_accuracy"]
    assert result["explicit"]["calls"][0]["stored_kind"] in {"lesson", "decision"}


def test_synonym_test_reports_keyword_fragility_and_explicit_stability():
    from experiments.benchmarks.round2.scenarios_v2 import SCENARIOS_V2
    from experiments.benchmarks.round2.synonym_test import run_synonym_test

    result = run_synonym_test(SCENARIOS_V2)

    assert result["pass"] is True
    assert result["keyword_overall_stability"] < 0.80
    assert result["explicit_overall_stability"] >= 0.80
    assert result["explicit_overall_stability"] - result["keyword_overall_stability"] >= 0.15


def test_near_synonym_test_classifies_decision_zone():
    from experiments.benchmarks.round2.near_synonym_test import run_near_synonym_test
    from experiments.benchmarks.round2.scenarios_v2 import SCENARIOS_V2

    result = run_near_synonym_test(SCENARIOS_V2)

    assert result["scenario_count"] == 10
    assert result["decision"] in {
        "协调器有价值",
        "灰色地带",
        "协调器是过度设计",
    }
    assert {group["group"] for group in result["groups"]} == {
        "lesson_decision_extract",
        "snapshot_context_user",
        "search_relevant_similar",
    }


def test_round2_runner_writes_report_and_json(tmp_path):
    from experiments.benchmarks.round2.run_round2 import run_round2

    result = run_round2(output_dir=tmp_path)

    assert result["scenario_count"] == 50
    assert (tmp_path / "results_d.json").exists()
    assert (tmp_path / "results_c.json").exists()
    assert (tmp_path / "results_e.json").exists()
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert "测试 D 结果" in report
    assert "测试 C 结果" in report
    assert "测试 E 结果" in report
    assert "综合决策" in report
