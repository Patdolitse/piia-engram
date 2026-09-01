from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "longitudinal_real_use_evidence.py"
SCRIPT = ROOT / "scripts" / "build_longitudinal_real_use_evidence.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("_longitudinal_real_use_evidence", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fv_record(ts: str, *, event: str = "onboard.scan.completed", surface: str = "cli", fields: dict | None = None) -> dict:
    return {
        "ts": ts,
        "event": event,
        "surface": surface,
        "client_tool": "codex",
        "fields": fields
        if fields is not None
        else {"repo_identity": "resolved", "outcome": "success", "error_category": "none"},
    }


def _write_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) if not isinstance(row, str) else row for row in rows) + "\n",
        encoding="utf-8",
    )


def _run_record(
    ts: str,
    *,
    run_id: str,
    status: str,
    checked: int = 0,
    valid: int = 0,
    invalid: int = 0,
    unknown: int = 0,
    demoted: int = 0,
    exit_code: int | None = 0,
    error_code: str | None = None,
) -> dict:
    return {
        "schema": "anchor_live_smoke_run_record.v1",
        "run_id": run_id,
        "timestamp": ts,
        "runner_status": status,
        "checked": checked,
        "valid": valid,
        "invalid": invalid,
        "unknown": unknown,
        "superseded": 0,
        "demoted_to_staging": demoted,
        "subprocess_exit": exit_code,
        "error_code": error_code,
        "evidence_ref": [],
    }


def _memory_eval_snapshot(*, passed: bool = True) -> dict:
    return {
        "schema": 1,
        "suite": "memory_eval_suite_v1",
        "public_safe": True,
        "overall_passed": passed,
        "recall": [
            {
                "fixture": "tests/fixtures/recall_eval_v1.json",
                "benchmark": "recall_eval_v1",
                "public_safe": True,
                "overall_passed": passed,
                "case_count": 2,
                "passed_count": 2 if passed else 1,
                "failed_count": 0 if passed else 1,
            }
        ],
        "admission": [
            {
                "fixture": "tests/fixtures/admission_guard_v1.json",
                "guard": "admission_guard_v1",
                "public_safe": True,
                "overall_passed": passed,
                "candidate_count": 3,
                "failed_expectation_count": 0 if passed else 1,
                "action_counts": {"accept": 3},
            }
        ],
        "agent_context_pack": {
            "schema": "agent_context_pack_eval.v1",
            "public_safe": True,
            "store_isolated": True,
            "overall_passed": passed,
            "case_count": 1,
            "passed_count": 1 if passed else 0,
            "failed_count": 0 if passed else 1,
        },
    }


def test_missing_inputs_and_empty_logs_are_insufficient_without_path_leak(tmp_path: Path) -> None:
    mod = _load_module()
    empty_first_value = tmp_path / "PRIVATE_PATH_SENTINEL_EMPTY.jsonl"
    empty_first_value.write_text("", encoding="utf-8")
    missing_anchor = tmp_path / "PRIVATE_PATH_SENTINEL_anchor.jsonl"
    missing_eval = tmp_path / "PRIVATE_PATH_SENTINEL_eval.json"

    artifact = mod.build_evidence(
        first_value_jsonl=empty_first_value,
        anchor_run_jsonl=missing_anchor,
        memory_eval_jsons=[missing_eval],
        as_of="2026-06-10",
        window_days=7,
    )
    body = json.dumps(artifact, ensure_ascii=False)

    assert artifact["coverage_readiness"]["status"] == "insufficient"
    assert artifact["evidence_classes"]["real_use_first_value"]["source_status"] == "empty"
    assert artifact["evidence_classes"]["real_use_first_value"]["valid_record_count"] == 0
    assert "PRIVATE_PATH_SENTINEL" not in body
    assert str(tmp_path) not in body


def test_unreadable_first_value_source_is_not_reported_as_empty(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module()
    first_value = tmp_path / "first_value_events.jsonl"
    first_value.write_text(json.dumps(_fv_record("2026-06-08T00:00:00Z")) + "\n", encoding="utf-8")
    original_open = mod.Path.open

    def blocked_open(self, *args, **kwargs):
        if mod.Path(self) == first_value:
            raise PermissionError("synthetic unreadable")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(mod.Path, "open", blocked_open)
    artifact = mod.build_evidence(first_value_jsonl=first_value, as_of="2026-06-10", window_days=7)
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert real["source_status"] == "source_unreadable"
    assert real["problem_counts"] == {"first_value.source_unreadable": 1}


def test_legal_multi_day_first_value_counts_active_days_and_span(tmp_path: Path) -> None:
    mod = _load_module()
    first_value = tmp_path / "first_value_events.jsonl"
    _write_jsonl(
        first_value,
        [
            _fv_record("2026-06-01T01:00:00Z"),
            _fv_record("2026-06-02T02:00:00Z", surface="mcp"),
            _fv_record("2026-06-08T03:00:00Z", event="recall.cross_tool.payoff", fields={
                "payoff": True,
                "current_tool": "codex",
                "source_relation": "cross_tool",
                "cross_tool_items_bucket": "1_3",
                "trusted_cross_tool_items_bucket": "1_3",
                "recall_surface": "get_recall",
            }),
        ],
    )

    artifact = mod.build_evidence(
        first_value_jsonl=first_value,
        as_of="2026-06-10",
        window_days=10,
    )
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert real["valid_record_count"] == 3
    assert real["active_utc_days"] == 3
    assert real["observed_span_days"] == 8
    assert real["first_observed_utc_date"] == "2026-06-01"
    assert real["last_observed_utc_date"] == "2026-06-08"
    assert real["event_counts"] == {
        "onboard.scan.completed": 2,
        "recall.cross_tool.payoff": 1,
    }
    assert real["surface_counts"] == {"cli": 2, "mcp": 1}


def test_first_value_invalid_shapes_never_count_as_valid_samples(tmp_path: Path) -> None:
    mod = _load_module()
    first_value = tmp_path / "first_value_events.jsonl"
    _write_jsonl(
        first_value,
        [
            "{not json",
            [],
            {**_fv_record("2026-06-02T00:00:00Z"), "extra": "safe_label"},
            _fv_record("2026-06-02T00:00:00Z", fields={"outcome": "not_a_closed_value"}),
            _fv_record("not-a-timestamp"),
            _fv_record("2026-06-02T00:00:00Z", fields={"outcome": True}),
            _fv_record(
                "2026-06-02T00:00:00Z",
                event="onboard.accept.completed",
                fields={"checked_anchor": "true"},
            ),
        ],
    )

    artifact = mod.build_evidence(first_value_jsonl=first_value, as_of="2026-06-10", window_days=7)
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert real["valid_record_count"] == 0
    assert real["invalid_record_count"] == 7
    assert real["event_counts"] == {}
    assert real["problem_counts"]["first_value.malformed_jsonl"] == 1
    assert real["problem_counts"]["first_value.type_confusion"] == 2
    assert artifact["coverage_readiness"]["status"] == "insufficient"


def test_unsafe_credential_path_and_content_records_are_blocked_without_echo(tmp_path: Path) -> None:
    mod = _load_module()
    sentinel = "PRIVATE_LONGITUDINAL_SENTINEL"
    first_value = tmp_path / "first_value_events.jsonl"
    _write_jsonl(
        first_value,
        [
            {**_fv_record("2026-06-02T00:00:00Z"), "Authorization": f"Bearer {sentinel}"},
            _fv_record("2026-06-02T00:00:00Z", fields={"repo_path": f"C:\\Users\\alice\\{sentinel}.txt"}),
            _fv_record("2026-06-02T00:00:00Z", fields={"query": f"please remember this private sentence {sentinel}"}),
        ],
    )

    artifact = mod.build_evidence(first_value_jsonl=first_value, as_of="2026-06-10", window_days=7)
    body = json.dumps(artifact, ensure_ascii=False)
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert real["valid_record_count"] == 0
    assert real["blocked_record_count"] == 2
    assert real["invalid_record_count"] == 3
    assert real["problem_counts"] == {
        "first_value.blocked_unsafe_content": 2,
        "first_value.unknown_field": 1,
    }
    assert sentinel not in body
    assert "Users" not in body


def test_live_smoke_uses_existing_run_contract_and_keeps_failures_out_of_anchor_samples(tmp_path: Path) -> None:
    mod = _load_module()
    run_jsonl = tmp_path / "anchor-runs.jsonl"
    _write_jsonl(
        run_jsonl,
        [
            _run_record("2026-06-08T00:00:00Z", run_id="stable-1", status="stable", checked=2, valid=2),
            _run_record("2026-06-08T01:00:00Z", run_id="down-1", status="downgrade", checked=2, valid=1, invalid=1),
            _run_record("2026-06-08T02:00:00Z", run_id="fail-1", status="failed", exit_code=None, error_code="timeout"),
            _run_record("2026-06-08T03:00:00Z", run_id="parse-1", status="parse_failed", exit_code=0, error_code="parse_failure"),
            _run_record("2026-06-08T04:00:00Z", run_id="bad-1", status="stable", checked=1, invalid=1),
            "{malformed without timestamp",
        ],
    )

    artifact = mod.build_evidence(anchor_run_jsonl=run_jsonl, as_of="2026-06-10", window_days=7)
    smoke = artifact["evidence_classes"]["operational_live_smoke"]

    assert smoke["runs"] == 5
    assert smoke["passed"] == 2
    assert smoke["failed"] == 3
    assert smoke["status_counts"] == {"downgrade": 1, "failed": 2, "parse_failed": 1, "stable": 1}
    assert smoke["failure_classes"] == {"invalid_run_record": 1, "parse_failure": 1, "timeout": 1}
    assert smoke["anchor_aggregate"]["checked"] == 4
    assert smoke["anchor_aggregate"]["valid"] == 3
    assert smoke["anchor_aggregate"]["invalid"] == 1
    assert smoke["problem_counts"]["operational_live_smoke.malformed_jsonl"] == 1
    assert smoke["contributes_to_real_use"] is False


def test_synthetic_eval_is_layered_and_never_lifts_zero_real_use_readiness(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    eval_json.write_text(json.dumps(_memory_eval_snapshot(passed=True)), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 1
    assert synthetic["passed_snapshot_count"] == 1
    assert synthetic["aggregate_case_counts"]["recall_case_count"] == 2
    assert synthetic["contributes_to_real_use"] is False
    assert artifact["coverage_readiness"]["status"] == "insufficient"


def test_synthetic_eval_rejects_top_level_pass_when_nested_section_failed(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["recall"][0]["overall_passed"] = False
    snapshot["recall"][0]["passed_count"] = 1
    snapshot["recall"][0]["failed_count"] = 1
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["passed_snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.inconsistent_overall": 1}


def test_synthetic_eval_rejects_top_level_fail_when_nested_sections_pass(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["overall_passed"] = False
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["failed_snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.inconsistent_overall": 1}


def test_synthetic_eval_accepts_consistent_failed_snapshot_as_failed_not_invalid(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    eval_json.write_text(json.dumps(_memory_eval_snapshot(passed=False)), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 1
    assert synthetic["passed_snapshot_count"] == 0
    assert synthetic["failed_snapshot_count"] == 1
    assert synthetic["invalid_snapshot_count"] == 0


def test_synthetic_eval_accepts_threshold_failure_with_clean_counts(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["overall_passed"] = False
    snapshot["recall"][0]["overall_passed"] = False
    snapshot["recall"][0]["mean_recall_at_k"] = 0.25
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 1
    assert synthetic["passed_snapshot_count"] == 0
    assert synthetic["failed_snapshot_count"] == 1
    assert synthetic["invalid_snapshot_count"] == 0
    assert synthetic["aggregate_case_counts"]["recall_failed_count"] == 0


def test_synthetic_eval_rejects_empty_sections_and_wrong_agent_schema(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["recall"] = []
    snapshot["admission"] = []
    snapshot["agent_context_pack"]["schema"] = "wrong-schema"
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["passed_snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.incomplete_suite": 1}


def test_synthetic_eval_rejects_zero_sample_sections(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["overall_passed"] = False
    snapshot["recall"][0]["overall_passed"] = False
    snapshot["recall"][0]["case_count"] = 0
    snapshot["recall"][0]["passed_count"] = 0
    snapshot["admission"][0]["candidate_count"] = 0
    snapshot["admission"][0]["action_counts"] = {}
    snapshot["agent_context_pack"]["overall_passed"] = False
    snapshot["agent_context_pack"]["case_count"] = 0
    snapshot["agent_context_pack"]["passed_count"] = 0
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.incomplete_suite": 1}


def test_synthetic_eval_rejects_admission_action_count_mismatch(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["admission"][0]["action_counts"] = {"accept": 1, "stage": 1}
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.inconsistent_counts": 1}


def test_synthetic_eval_rejects_bool_action_counts(tmp_path: Path) -> None:
    mod = _load_module()
    eval_json = tmp_path / "memory-eval.json"
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["admission"][0]["action_counts"] = {"accept": True, "stage": 2}
    eval_json.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[eval_json], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.invalid_count": 1}


def test_real_memory_eval_snapshot_is_accepted(tmp_path: Path) -> None:
    output = tmp_path / "memory-eval.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "run_memory_evals.py"), "--json", "--output", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    mod = _load_module()

    artifact = mod.build_evidence(memory_eval_jsons=[output], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 1
    assert synthetic["passed_snapshot_count"] == 1
    assert synthetic["invalid_snapshot_count"] == 0
    assert synthetic["aggregate_case_counts"]["recall_case_count"] == 21  # v4.20: +3 real-get_recall playbook cases
    assert synthetic["aggregate_case_counts"]["admission_candidate_count"] == 15
    assert synthetic["aggregate_case_counts"]["agent_context_case_count"] == 2


def test_live_smoke_unsafe_extra_field_is_failed_without_anchor_contribution(tmp_path: Path) -> None:
    mod = _load_module()
    sentinel = "PRIVATE_LIVE_SMOKE_SENTINEL"
    run_jsonl = tmp_path / "anchor-runs.jsonl"
    unsafe = _run_record("2026-06-08T00:00:00Z", run_id="stable-unsafe", status="stable", checked=2, valid=2)
    unsafe["debug_path"] = f"C:\\Users\\alice\\{sentinel}.json"
    _write_jsonl(run_jsonl, [unsafe])

    artifact = mod.build_evidence(anchor_run_jsonl=run_jsonl, as_of="2026-06-10", window_days=7)
    body = json.dumps(artifact, ensure_ascii=False)
    smoke = artifact["evidence_classes"]["operational_live_smoke"]

    assert smoke["runs"] == 1
    assert smoke["passed"] == 0
    assert smoke["failed"] == 1
    assert smoke["status_counts"] == {"parse_failed": 1}
    assert smoke["failure_classes"] == {"unsafe_record": 1}
    assert smoke["anchor_aggregate"]["checked"] == 0
    assert smoke["problem_counts"] == {"operational_live_smoke.blocked_unsafe_content": 1}
    assert sentinel not in body
    assert "Users" not in body


def test_live_smoke_unsafe_without_trusted_timestamp_stays_source_level(tmp_path: Path) -> None:
    mod = _load_module()
    sentinel = "PRIVATE_LIVE_SMOKE_NO_TS"
    run_jsonl = tmp_path / "anchor-runs.jsonl"
    unsafe = _run_record("not-a-timestamp", run_id="stable-unsafe", status="stable", checked=2, valid=2)
    unsafe["Authorization"] = f"Bearer {sentinel}"
    _write_jsonl(run_jsonl, [unsafe])

    artifact = mod.build_evidence(anchor_run_jsonl=run_jsonl, as_of="2026-06-10", window_days=7)
    body = json.dumps(artifact, ensure_ascii=False)
    smoke = artifact["evidence_classes"]["operational_live_smoke"]

    assert smoke["runs"] == 0
    assert smoke["passed"] == 0
    assert smoke["failed"] == 0
    assert smoke["window_record_count"] == 0
    assert smoke["problem_counts"] == {"operational_live_smoke.blocked_unsafe_content": 1}
    assert sentinel not in body


def test_synthetic_eval_blocks_posix_absolute_paths_but_not_relative_fixture_paths(tmp_path: Path) -> None:
    mod = _load_module()
    good = tmp_path / "good-memory-eval.json"
    bad = tmp_path / "bad-memory-eval.json"
    good.write_text(json.dumps(_memory_eval_snapshot(passed=True)), encoding="utf-8")
    snapshot = _memory_eval_snapshot(passed=True)
    snapshot["recall"][0]["fixture"] = "/etc/engram/private-fixture.json"
    bad.write_text(json.dumps(snapshot), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[good, bad], as_of="2026-06-10", window_days=7)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 1
    assert synthetic["passed_snapshot_count"] == 1
    assert synthetic["invalid_snapshot_count"] == 1
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.blocked_unsafe_content": 1}


def test_credential_label_variants_are_blocked_without_echo(tmp_path: Path) -> None:
    mod = _load_module()
    sentinel = "PRIVATE_CREDENTIAL_SENTINEL"
    by_key = tmp_path / "by-key.json"
    by_value = tmp_path / "by-value.json"
    snapshot_key = _memory_eval_snapshot(passed=True)
    snapshot_key["secret_key"] = sentinel
    snapshot_value = _memory_eval_snapshot(passed=True)
    snapshot_value["recall"][0]["fixture"] = f"token-key-{sentinel}"
    by_key.write_text(json.dumps(snapshot_key), encoding="utf-8")
    by_value.write_text(json.dumps(snapshot_value), encoding="utf-8")

    artifact = mod.build_evidence(memory_eval_jsons=[by_key, by_value], as_of="2026-06-10", window_days=7)
    body = json.dumps(artifact, ensure_ascii=False)
    synthetic = artifact["evidence_classes"]["synthetic_memory_eval"]

    assert synthetic["snapshot_count"] == 0
    assert synthetic["invalid_snapshot_count"] == 2
    assert synthetic["problem_counts"] == {"synthetic_memory_eval.blocked_unsafe_content": 2}
    assert sentinel not in body


def test_readiness_thresholds_are_real_use_only_and_deterministic(tmp_path: Path) -> None:
    mod = _load_module()
    first_value = tmp_path / "first_value_events.jsonl"
    _write_jsonl(first_value, [_fv_record("2026-06-08T00:00:00Z")])
    partial = mod.build_evidence(first_value_jsonl=first_value, as_of="2026-06-10", window_days=7)
    assert partial["coverage_readiness"]["status"] == "partial"

    _write_jsonl(
        first_value,
        [
            _fv_record("2026-06-01T00:00:00Z"),
            _fv_record("2026-06-03T00:00:00Z"),
            _fv_record("2026-06-05T00:00:00Z"),
            _fv_record("2026-06-07T00:00:00Z"),
            _fv_record("2026-06-09T00:00:00Z"),
            _fv_record("2026-06-11T00:00:00Z"),
            _fv_record("2026-06-14T00:00:00Z"),
        ],
    )
    ready = mod.build_evidence(first_value_jsonl=first_value, as_of="2026-06-14", window_days=14)
    assert ready["coverage_readiness"]["status"] == "longitudinal_ready"
    assert ready["coverage_readiness"]["thresholds"] == {
        "partial_min_active_utc_days": 1,
        "longitudinal_ready_min_active_utc_days": 7,
        "longitudinal_ready_min_observed_span_days": 14,
    }


def test_duplicate_first_value_rows_are_counted_without_fake_deduplication(tmp_path: Path) -> None:
    mod = _load_module()
    first_value = tmp_path / "first_value_events.jsonl"
    row = _fv_record("2026-06-08T00:00:00Z")
    _write_jsonl(first_value, [row, row])

    artifact = mod.build_evidence(first_value_jsonl=first_value, as_of="2026-06-10", window_days=7)
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert real["valid_record_count"] == 2
    assert real["event_counts"] == {"onboard.scan.completed": 2}
    assert real["deduplication_performed"] is False
    assert real["event_id_observed"] is False


def test_first_value_source_authenticity_limits_are_explicit() -> None:
    mod = _load_module()
    artifact = mod.build_evidence(as_of="2026-06-10", window_days=7)
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert real["source_authenticity_verified"] is False
    assert real["append_only_integrity_verified"] is False
    assert "owner-local closed-schema event observation" in real["coverage_semantics"]
    assert "independently_verified_real_use" in artifact["claim_boundary"]["prohibited"]


def test_as_of_timestamp_window_filters_by_trusted_record_time(tmp_path: Path) -> None:
    mod = _load_module()
    first_value = tmp_path / "first_value_events.jsonl"
    _write_jsonl(
        first_value,
        [
            _fv_record("2026-06-07T23:59:59Z"),
            _fv_record("2026-06-08T00:00:00Z"),
            _fv_record("2026-06-10T12:00:00Z"),
            _fv_record("2026-06-10T12:00:01Z"),
        ],
    )

    artifact = mod.build_evidence(
        first_value_jsonl=first_value,
        as_of="2026-06-10T12:00:00Z",
        window_days=3,
    )
    real = artifact["evidence_classes"]["real_use_first_value"]

    assert artifact["window_start_utc"] == "2026-06-08T00:00:00Z"
    assert artifact["window_end_utc"] == "2026-06-10T12:00:00Z"
    assert real["valid_record_count"] == 2
    assert real["valid_records_outside_window_count"] == 2


def test_window_bounds_overflow_fails_closed_without_traceback_or_value_echo(tmp_path: Path) -> None:
    mod = _load_module()
    with pytest.raises(ValueError):
        mod.window_bounds("0001-01-01", 999999999)

    caller_value = "0001-01-01"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--as-of",
            caller_value,
            "--window-days",
            "999999999",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "invalid longitudinal evidence arguments" in result.stderr
    assert caller_value not in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_json_text_and_output_are_deterministic_and_path_independent(tmp_path: Path) -> None:
    first_value = tmp_path / "first_value_events.jsonl"
    output = tmp_path / "artifact.json"
    _write_jsonl(first_value, [_fv_record("2026-06-08T00:00:00Z")])

    command = [
        sys.executable,
        str(SCRIPT),
        "--first-value-jsonl",
        str(first_value),
        "--as-of",
        "2026-06-10",
        "--window-days",
        "7",
        "--output",
        str(output),
        "--json",
    ]
    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)

    assert first.stdout == second.stdout == output.read_text(encoding="utf-8")
    assert str(tmp_path) not in first.stdout
    text = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--first-value-jsonl",
            str(first_value),
            "--as-of",
            "2026-06-10",
            "--window-days",
            "7",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Longitudinal real-use evidence" in text.stdout
    assert str(tmp_path) not in text.stdout


def test_default_safety_flags_are_false() -> None:
    mod = _load_module()
    artifact = mod.build_evidence(as_of="2026-06-10", window_days=7)

    assert artifact["safety_flags"] == {
        "network_call_performed": False,
        "remote_telemetry_sent": False,
        "store_write_performed": False,
        "claim_queue_write_performed": False,
        "memory_write_performed": False,
        "public_export_performed": False,
        "validation_runner_executed": False,
    }


def test_private_internal_artifact_is_not_wired_into_public_export_or_release_surfaces() -> None:
    needle = "build_longitudinal_real_use_evidence"
    release_and_export_files = [
        ROOT / "scripts" / "build_release_dossier.py",
        ROOT / "scripts" / "release_orchestrator.py",
        ROOT / "scripts" / "check_generated_export_redaction.py",
        ROOT / "src" / "piia_engram" / "agents_md_export.py",
        ROOT / "src" / "piia_engram" / "reports_identity.py",
    ]

    for path in release_and_export_files:
        assert needle not in path.read_text(encoding="utf-8")
