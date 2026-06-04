"""Tests for MCIC scenario definitions and Hermes A/B v2 contract.

Covers:
- Scenario invariants (count, fields, categories, synthetic-only)
- Scenario validation edge cases (missing fields, bad category, private data)
- A/B run validation (isolation, negative controls, latency, judge fields)
- Scoring (sub-score separation, negative-control-as-validity, latency budget)
- Public summary rendering (no private data, structural checks)
"""

from __future__ import annotations

import pytest

from piia_engram.continuity_contract import (
    AB_RUN_REQUIRED_FIELDS,
    DEFAULT_LATENCY_BUDGET_MS,
    MCIC_CATEGORIES,
    SCENARIO_REQUIRED_FIELDS,
    _PRIVATE_MARKERS,
    default_mcic_scenarios,
    render_public_summary,
    score_ab_result,
    validate_ab_run,
    validate_scenario,
)

# ---------------------------------------------------------------------------
# Helpers - build synthetic A/B runs for testing
# ---------------------------------------------------------------------------


def _make_result(
    scenario_id: str,
    arm: str,
    *,
    latency_ms: float = 100.0,
    judge_verdict: str = "pass",
    evidence: str = "synthetic evidence",
    **overrides,
) -> dict:
    r = {
        "scenario_id": scenario_id,
        "arm": arm,
        "session_dir": f"/tmp/sess_{scenario_id}_{arm}",
        "home_dir": f"/tmp/home_{scenario_id}_{arm}",
        "response": f"Synthetic response for {scenario_id}/{arm}",
        "latency_ms": latency_ms,
        "tool_calls": [],
        "judge_verdict": judge_verdict,
        "evidence": evidence,
    }
    r.update(overrides)
    return r


def _make_valid_run(
    scenarios=None,
    *,
    latency_engram: float = 150.0,
    latency_baseline: float = 100.0,
):
    if scenarios is None:
        scenarios = default_mcic_scenarios()
    results = []
    for s in scenarios:
        if s["category"] == "negative_control" and s["id"] == "NC-01":
            ev, bv = "fail", "fail"
        elif s["category"] == "negative_control":
            ev, bv = "pass", "fail"
        else:
            ev, bv = "pass", "fail"
        results.append(
            _make_result(s["id"], "engram",
                         latency_ms=latency_engram, judge_verdict=ev)
        )
        results.append(
            _make_result(s["id"], "baseline",
                         latency_ms=latency_baseline, judge_verdict=bv)
        )
    return {"scenarios": scenarios, "results": results}


def _fake_windows_user_path(*parts: str) -> str:
    return "C:" + "\\" + "Users" + "\\" + "\\".join(parts)


def _fake_posix_home_path(*parts: str) -> str:
    return "/" + "home/" + "/".join(parts)


def _fake_macos_user_path(*parts: str) -> str:
    return "/" + "Users/" + "/".join(parts)


def _fake_api_key(prefix: str = "sk") -> str:
    if prefix == "sk-proj":
        return "sk-" + "proj-" + "AAAA1234abcd"
    return "sk-" + "A" * 24


def _fake_email() -> str:
    return "test" + "@" + "example.com"


# ===================================================================
# 1. Scenario invariants
# ===================================================================


class TestMCICScenarios:

    def test_at_least_10_scenarios(self):
        assert len(default_mcic_scenarios()) >= 10

    def test_all_required_fields_present(self):
        for s in default_mcic_scenarios():
            missing = SCENARIO_REQUIRED_FIELDS - set(s.keys())
            assert not missing, f"{s.get('id')}: missing {missing}"

    def test_all_categories_covered(self):
        cats = {s["category"] for s in default_mcic_scenarios()}
        assert cats == MCIC_CATEGORIES

    def test_unique_ids(self):
        ids = [s["id"] for s in default_mcic_scenarios()]
        assert len(ids) == len(set(ids))

    def test_synthetic_data_only(self):
        for s in default_mcic_scenarios():
            for field in SCENARIO_REQUIRED_FIELDS - {"id", "category"}:
                text = s.get(field, "")
                assert not _PRIVATE_MARKERS.search(text), (
                    f"{s['id']}.{field} contains private marker"
                )

    def test_validate_passes_for_all_defaults(self):
        for s in default_mcic_scenarios():
            issues = validate_scenario(s)
            assert issues == [], f"{s['id']}: {issues}"

    def test_each_id_has_category_prefix(self):
        prefix_map = {
            "explicit_recall": "ER-",
            "implicit_personalization": "IP-",
            "adversarial_false_premise": "AF-",
            "negative_control": "NC-",
            "latency": "LT-",
        }
        for s in default_mcic_scenarios():
            expected = prefix_map.get(s["category"], "")
            assert s["id"].startswith(expected), (
                f"{s['id']} should start with {expected}"
            )


# ===================================================================
# 2. Scenario validation edge cases
# ===================================================================


class TestValidateScenario:

    def test_missing_fields(self):
        issues = validate_scenario({"id": "X"})
        assert any("missing fields" in i for i in issues)

    def test_unknown_category(self):
        s = dict.fromkeys(SCENARIO_REQUIRED_FIELDS, "x")
        s["id"] = "BAD"
        s["category"] = "invented_category"
        issues = validate_scenario(s)
        assert any("unknown category" in i for i in issues)

    @pytest.mark.parametrize("field", [
        "client_a_action", "client_b_prompt", "expected_signal",
    ])
    def test_private_windows_path(self, field):
        s = dict.fromkeys(SCENARIO_REQUIRED_FIELDS, "clean text")
        s["id"] = "PRIV"
        s["category"] = "explicit_recall"
        s[field] = "Check " + _fake_windows_user_path("realuser", "secrets")
        issues = validate_scenario(s)
        assert any("private marker" in i for i in issues)

    def test_private_api_key(self):
        s = dict.fromkeys(SCENARIO_REQUIRED_FIELDS, "clean text")
        s["id"] = "KEY"
        s["category"] = "explicit_recall"
        s["client_a_action"] = "Store " + _fake_api_key("sk-proj")
        assert any("private marker" in i for i in validate_scenario(s))

    def test_private_linux_path(self):
        s = dict.fromkeys(SCENARIO_REQUIRED_FIELDS, "clean text")
        s["id"] = "LIN"
        s["category"] = "explicit_recall"
        s["client_b_prompt"] = "Read " + _fake_posix_home_path("alice", "config")
        assert any("private marker" in i for i in validate_scenario(s))

    def test_private_email(self):
        s = dict.fromkeys(SCENARIO_REQUIRED_FIELDS, "clean text")
        s["id"] = "EML"
        s["category"] = "explicit_recall"
        s["client_a_action"] = "Store " + _fake_email() + " as contact"
        assert any("private marker" in i for i in validate_scenario(s))

    def test_private_marker_in_failure_mode(self):
        s = dict.fromkeys(SCENARIO_REQUIRED_FIELDS, "clean text")
        s["id"] = "FAIL"
        s["category"] = "explicit_recall"
        s["failure_mode"] = "Leaks " + _fake_windows_user_path("realuser", "secrets")
        assert any("private marker" in i for i in validate_scenario(s))

    def test_clean_scenario_no_issues(self):
        s = {
            "id": "OK-01",
            "category": "explicit_recall",
            "client_a_action": "Store a preference",
            "client_b_prompt": "Recall it",
            "expected_signal": "Preference returned",
            "failure_mode": "Not returned",
        }
        assert validate_scenario(s) == []


# ===================================================================
# 3. A/B run validation
# ===================================================================


class TestValidateABRun:

    def test_valid_run_passes(self):
        v = validate_ab_run(_make_valid_run())
        assert v["valid"] is True
        assert v["blocking"] == []

    def test_empty_scenarios_blocked(self):
        v = validate_ab_run({"scenarios": [], "results": [{"x": 1}]})
        assert v["valid"] is False
        assert any("scenarios" in b for b in v["blocking"])

    def test_empty_results_blocked(self):
        v = validate_ab_run({
            "scenarios": default_mcic_scenarios(),
            "results": [],
        })
        assert v["valid"] is False

    def test_missing_scenarios_key(self):
        v = validate_ab_run({"results": [{"x": 1}]})
        assert v["valid"] is False

    def test_session_dir_reuse_blocked(self):
        run = _make_valid_run()
        run["results"][0]["session_dir"] = run["results"][2]["session_dir"]
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("session_dir reused" in b for b in v["blocking"])

    def test_home_dir_reuse_blocked(self):
        run = _make_valid_run()
        run["results"][0]["home_dir"] = run["results"][2]["home_dir"]
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("home_dir reused" in b for b in v["blocking"])

    def test_missing_negative_control(self):
        scenarios = [
            s for s in default_mcic_scenarios()
            if s["category"] != "negative_control"
        ]
        run = _make_valid_run(scenarios=scenarios)
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("negative_control" in b for b in v["blocking"])

    def test_missing_latency_field(self):
        run = _make_valid_run()
        del run["results"][0]["latency_ms"]
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("latency_ms" in b for b in v["blocking"])

    def test_non_numeric_latency(self):
        run = _make_valid_run()
        run["results"][0]["latency_ms"] = "slow"
        v = validate_ab_run(run)
        assert v["valid"] is False

    def test_invalid_arm_blocked(self):
        run = _make_valid_run()
        run["results"][0]["arm"] = "neither"
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("arm must be" in b for b in v["blocking"])

    def test_missing_judge_verdict_blocked(self):
        run = _make_valid_run()
        del run["results"][0]["judge_verdict"]
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("judge_verdict" in b for b in v["blocking"])

    def test_missing_evidence_blocked(self):
        run = _make_valid_run()
        del run["results"][0]["evidence"]
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("evidence" in b for b in v["blocking"])

    def test_private_marker_in_evidence_blocked(self):
        run = _make_valid_run()
        run["results"][0]["evidence"] = (
            "Found at " + _fake_posix_home_path("alice", "data")
        )
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("private marker in evidence" in b for b in v["blocking"])

    def test_private_marker_in_response_blocked(self):
        run = _make_valid_run()
        run["results"][0]["response"] = (
            "Read " + _fake_windows_user_path("realuser", "data")
        )
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("private marker in response" in b for b in v["blocking"])

    def test_unknown_scenario_id_blocked(self):
        run = _make_valid_run()
        run["results"][0]["scenario_id"] = "UNKNOWN"
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any("scenario_id not defined" in b for b in v["blocking"])

    def test_missing_arm_result_blocked(self):
        run = _make_valid_run()
        run["results"] = [
            r for r in run["results"]
            if not (r["scenario_id"] == "ER-01" and r["arm"] == "baseline")
        ]
        v = validate_ab_run(run)
        assert v["valid"] is False
        assert any(
            "scenario ER-01: missing baseline result" in b
            for b in v["blocking"]
        )


# ===================================================================
# 4. Scoring
# ===================================================================


class TestScoreABResult:

    def test_separate_sub_scores(self):
        scores = score_ab_result(_make_valid_run())
        assert "explicit_recall" in scores
        assert "implicit_personalization" in scores
        assert "adversarial_correction" in scores
        assert "negative_control_validity" in scores
        assert "latency" in scores

    def test_schema_version(self):
        scores = score_ab_result(_make_valid_run())
        assert scores["schema"] == 1

    def test_explicit_recall_rate(self):
        scores = score_ab_result(_make_valid_run())
        er = scores["explicit_recall"]
        assert er["total"] == 4
        assert er["passed"] == 4
        assert er["rate"] == 1.0

    def test_adversarial_correction_rate(self):
        scores = score_ab_result(_make_valid_run())
        ac = scores["adversarial_correction"]
        assert ac["total"] == 3
        assert ac["passed"] == 3

    def test_negative_control_is_validity_not_product_win(self):
        scores = score_ab_result(_make_valid_run())
        assert scores["summary"]["negative_controls_are_validity_checks"]

    def test_negative_control_valid_in_clean_run(self):
        scores = score_ab_result(_make_valid_run())
        assert scores["negative_control_validity"]["valid"] is True

    def test_negative_control_invalid_when_nc01_engram_passes(self):
        run = _make_valid_run()
        for r in run["results"]:
            if r["scenario_id"] == "NC-01" and r["arm"] == "engram":
                r["judge_verdict"] = "pass"
        scores = score_ab_result(run)
        assert scores["negative_control_validity"]["valid"] is False
        details = scores["negative_control_validity"]["details"]
        nc01 = [d for d in details if d["scenario_id"] == "NC-01"][0]
        assert "fabricated" in nc01["issue"]

    def test_negative_control_invalid_when_nc01_baseline_passes(self):
        run = _make_valid_run()
        for r in run["results"]:
            if r["scenario_id"] == "NC-01" and r["arm"] == "baseline":
                r["judge_verdict"] = "pass"
        scores = score_ab_result(run)
        assert scores["negative_control_validity"]["valid"] is False
        details = scores["negative_control_validity"]["details"]
        nc01 = [d for d in details if d["scenario_id"] == "NC-01"][0]
        assert "baseline fabricated" in nc01["issue"]

    def test_negative_control_invalid_when_nc02_baseline_passes(self):
        run = _make_valid_run()
        for r in run["results"]:
            if r["scenario_id"] == "NC-02" and r["arm"] == "baseline":
                r["judge_verdict"] = "pass"
        scores = score_ab_result(run)
        assert scores["negative_control_validity"]["valid"] is False

    def test_latency_overhead_explicit(self):
        scores = score_ab_result(
            _make_valid_run(latency_engram=250.0, latency_baseline=100.0)
        )
        lat = scores["latency"]
        assert lat["overhead_ms"] == pytest.approx(150.0)
        assert lat["budget_ok"] is True
        assert scores["summary"]["latency_overhead_explicit"] is True

    def test_latency_over_budget(self):
        scores = score_ab_result(
            _make_valid_run(latency_engram=3000.0, latency_baseline=100.0)
        )
        lat = scores["latency"]
        assert lat["overhead_ms"] == pytest.approx(2900.0)
        assert lat["budget_ok"] is False

    def test_latency_budget_value(self):
        scores = score_ab_result(_make_valid_run())
        assert scores["latency"]["budget_ms"] == DEFAULT_LATENCY_BUDGET_MS

    def test_empty_run_scores_gracefully(self):
        scores = score_ab_result({"scenarios": [], "results": []})
        assert scores["explicit_recall"]["total"] == 0
        assert scores["explicit_recall"]["rate"] is None
        assert scores["latency"]["overhead_ms"] is None

    def test_partial_verdicts(self):
        run = _make_valid_run()
        fail_count = 0
        for r in run["results"]:
            if (
                r["scenario_id"] == "ER-01"
                and r["arm"] == "engram"
            ):
                r["judge_verdict"] = "fail"
                fail_count += 1
        scores = score_ab_result(run)
        assert scores["explicit_recall"]["passed"] == 3
        assert scores["explicit_recall"]["rate"] == 0.75


# ===================================================================
# 5. Public summary rendering
# ===================================================================


class TestRenderPublicSummary:

    def test_contains_all_sub_scores(self):
        run = _make_valid_run()
        scores = score_ab_result(run)
        text = render_public_summary(run, scores)
        assert "explicit_recall" in text
        assert "implicit_personalization" in text
        assert "adversarial_correction" in text
        assert "negative_control_validity" in text
        assert "latency_overhead" in text

    def test_no_private_markers(self):
        run = _make_valid_run()
        scores = score_ab_result(run)
        text = render_public_summary(run, scores)
        assert not _PRIVATE_MARKERS.search(text)

    def test_no_local_paths(self):
        run = _make_valid_run()
        scores = score_ab_result(run)
        text = render_public_summary(run, scores)
        assert "/tmp/" not in text
        assert "C:\\" not in text
        assert "\\Users\\" not in text

    def test_notes_section_present(self):
        run = _make_valid_run()
        scores = score_ab_result(run)
        text = render_public_summary(run, scores)
        assert "Negative controls are validity checks" in text
        assert "Latency overhead is reported separately" in text
        assert "synthetic" in text.lower()

    def test_scenario_count_in_summary(self):
        run = _make_valid_run()
        scores = score_ab_result(run)
        text = render_public_summary(run, scores)
        assert f"Scenarios: {len(run['scenarios'])}" in text


# ===================================================================
# 6. Private-marker regex coverage
# ===================================================================


class TestPrivateMarkers:

    @pytest.mark.parametrize("text,should_match", [
        (_fake_api_key("sk-proj"), True),
        (_fake_api_key(), True),
        (_fake_windows_user_path("john", "docs"), True),
        (_fake_posix_home_path("alice", ".config"), True),
        (_fake_macos_user_path("Bob", "Library"), True),
        (_fake_email(), True),
        ("just normal text", False),
        ("UTC+8", False),
        ("PostgreSQL", False),
        ("preferred_language=Rust", False),
    ])
    def test_marker_detection(self, text, should_match):
        matched = _PRIVATE_MARKERS.search(text) is not None
        assert matched == should_match, f"{text!r}: expected {should_match}"
