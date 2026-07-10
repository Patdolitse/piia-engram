"""Private longitudinal real-use evidence aggregation.

This module builds an owner-review-only artifact from explicit local inputs.
It is deliberately read-only: no disk scanning, no network calls, no Engram
store reads/writes, and no public export wiring.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (SRC, SCRIPT_DIR):
    _text = str(_path)
    if _text not in sys.path:
        sys.path.insert(0, _text)

from piia_engram.telemetry import FIRST_VALUE_SCHEMA, _FV_SURFACES, _FV_TOOLS  # noqa: E402

import collect_anchor_live_smoke_evidence as anchor_evidence  # noqa: E402


SCHEMA = "longitudinal_real_use_evidence.v1"
EVALUATION_KIND = "longitudinal_real_use"

READINESS_THRESHOLDS = {
    "partial_min_active_utc_days": 1,
    "longitudinal_ready_min_active_utc_days": 7,
    "longitudinal_ready_min_observed_span_days": 14,
}

SAFETY_FLAGS = {
    "network_call_performed": False,
    "remote_telemetry_sent": False,
    "store_write_performed": False,
    "claim_queue_write_performed": False,
    "memory_write_performed": False,
    "public_export_performed": False,
    "validation_runner_executed": False,
}

_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)
_UTC_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_PRIVATE_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/][^\s\"'<>|]+"),
    re.compile(r"\\\\[^\\/\s\"'<>|]+[\\/][^\\/\s\"'<>|]+"),
    re.compile(r"(?i)(^|[\s\"'=:])/(?!/)[A-Za-z0-9._-]+(?:/[^\s\"'<>]*)?"),
    re.compile(r"(?i)(^|[\\/])\.\.([\\/]|$)"),
    re.compile(r"(?i)\bhttps?://[^\s\"'<>]+"),
    re.compile(r"(?i)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*['\"]?[A-Za-z][A-Za-z0-9._~+/=-]*"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(
        r"(?ix)\b("
        r"api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
        r"password|passwd|pwd|token|secret|client[_-]?secret|private[_-]?key"
        r")\b\s*[:=]\s*['\"]?[^\s\"'<>|]+"
    ),
)
_PRIVATE_KEY_RE = re.compile(
    r"(?ix)\b("
    r"authorization|credentials?|"
    r"api[_-]?key|apiKey|"
    r"access[_-]?(?:key|token)|accessKey|accessToken|"
    r"auth[_-]?token|authToken|refresh[_-]?token|refreshToken|"
    r"bearer[_-]?token|bearerToken|"
    r"client[_-]?secret|clientSecret|"
    r"secret[_-]?key|secretKey|"
    r"private[_-]?key|privateKey|"
    r"password|passwd|pwd|token|secret"
    r")\b"
)


def _format_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_as_of(value: str) -> datetime:
    """Parse an explicit UTC date/timestamp for deterministic artifacts."""
    text = str(value or "").strip()
    if _UTC_DATE.fullmatch(text):
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("as_of must be a UTC date or timestamp") from exc
        return datetime.combine(parsed_date, time(23, 59, 59), tzinfo=timezone.utc)
    parsed = _parse_utc_timestamp(text)
    if parsed is None:
        raise ValueError("as_of must be a UTC date or timestamp")
    return parsed


def window_bounds(as_of: str, window_days: int) -> tuple[datetime, datetime]:
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days <= 0:
        raise ValueError("window_days must be a positive integer")
    end = parse_as_of(as_of)
    try:
        start_date = end.date() - timedelta(days=window_days - 1)
        start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("window bounds out of supported range") from exc
    return start, end


def _parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _UTC_TIMESTAMP.fullmatch(text):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        return None
    return parsed.astimezone(timezone.utc)


def _in_window(ts: datetime, start: datetime, end: datetime) -> bool:
    return start <= ts <= end


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def _field_value_label(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _merge_problem_counts(*blocks: dict[str, int]) -> list[dict[str, int | str]]:
    merged: Counter[str] = Counter()
    for block in blocks:
        merged.update({str(key): int(value) for key, value in block.items() if int(value) > 0})
    return [{"code": key, "count": merged[key]} for key in sorted(merged)]


def _source_status(
    *,
    provided: bool,
    exists: bool,
    empty: bool,
    valid: int,
    invalid: int,
    unreadable: bool = False,
) -> str:
    if not provided or not exists:
        return "source_missing"
    if unreadable:
        return "source_unreadable"
    if empty and valid == 0 and invalid == 0:
        return "empty"
    if valid > 0 and invalid > 0:
        return "partial"
    if valid > 0:
        return "available"
    return "no_valid_records"


def _strict_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _strict_positive_int(value: Any) -> int | None:
    parsed = _strict_non_negative_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _strict_action_counts(value: Any) -> tuple[int | None, str]:
    if not isinstance(value, dict):
        return None, "synthetic_memory_eval.invalid_count"
    total = 0
    for key, count in value.items():
        if not isinstance(key, str) or not key:
            return None, "synthetic_memory_eval.invalid_count"
        parsed = _strict_non_negative_int(count)
        if parsed is None:
            return None, "synthetic_memory_eval.invalid_count"
        total += parsed
    return total, ""


def _unsafe_string(text: str) -> bool:
    return (
        _PRIVATE_KEY_RE.search(text) is not None
        or any(pattern.search(text) for pattern in _PRIVATE_PATTERNS)
    )


def _contains_unsafe(value: Any) -> bool:
    if isinstance(value, str):
        return _unsafe_string(value)
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and _unsafe_string(key):
                return True
            if _contains_unsafe(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_unsafe(item) for item in value)
    return False


def _record_unsafe_live_smoke_window_run(live_smoke: dict[str, Any]) -> None:
    anchor_evidence._record_failed_run(live_smoke, "parse_failed", "unsafe_record")


def _validate_first_value_record(record: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(record, dict):
        return None, "first_value.non_object_json"
    if _contains_unsafe(record):
        return None, "first_value.blocked_unsafe_content"

    allowed_top = {"ts", "event", "surface", "fields", "client_tool"}
    if set(record) - allowed_top:
        return None, "first_value.unknown_top_level_field"

    ts = _parse_utc_timestamp(record.get("ts"))
    if ts is None:
        return None, "first_value.invalid_timestamp"

    event = record.get("event")
    if not isinstance(event, str) or event not in FIRST_VALUE_SCHEMA:
        return None, "first_value.invalid_event"

    surface = record.get("surface")
    if not isinstance(surface, str) or surface not in _FV_SURFACES:
        return None, "first_value.invalid_surface"

    client_tool = record.get("client_tool")
    if "client_tool" in record and (
        not isinstance(client_tool, str) or client_tool not in _FV_TOOLS
    ):
        return None, "first_value.invalid_client_tool"

    fields = record.get("fields")
    if not isinstance(fields, dict):
        return None, "first_value.invalid_fields"
    if _contains_unsafe(fields):
        return None, "first_value.blocked_unsafe_content"

    schema = FIRST_VALUE_SCHEMA[event]
    clean_fields: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in schema:
            return None, "first_value.unknown_field"
        allowed = schema[key]
        if allowed is bool:
            if not isinstance(value, bool):
                return None, "first_value.type_confusion"
            clean_fields[key] = value
            continue
        if not isinstance(value, str):
            return None, "first_value.type_confusion"
        if value not in allowed:
            return None, "first_value.invalid_closed_value"
        clean_fields[key] = value

    return {
        "ts": ts,
        "event": event,
        "surface": surface,
        "client_tool": client_tool if isinstance(client_tool, str) else "",
        "fields": clean_fields,
    }, ""


def collect_real_use_first_value(
    path: str | Path | None,
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
) -> dict[str, Any]:
    provided = path is not None and str(path) != ""
    exists = provided and Path(path).is_file()
    problem_counts: Counter[str] = Counter()
    valid_source_count = 0
    invalid_count = 0
    blocked_count = 0
    outside_window_count = 0
    saw_line = False
    unreadable = False

    active_dates: set[str] = set()
    event_counts: Counter[str] = Counter()
    surface_counts: Counter[str] = Counter()
    client_tool_counts: Counter[str] = Counter()
    field_value_counts: dict[str, dict[str, Counter[str]]] = {}

    if not provided or not exists:
        problem_counts["first_value.source_missing"] += 1
    else:
        try:
            handle = Path(path).open("r", encoding="utf-8")
        except Exception:
            unreadable = True
            problem_counts["first_value.source_unreadable"] += 1
        else:
            try:
                with handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        saw_line = True
                        try:
                            loaded = json.loads(line)
                        except Exception:
                            invalid_count += 1
                            problem_counts["first_value.malformed_jsonl"] += 1
                            continue
                        clean, code = _validate_first_value_record(loaded)
                        if clean is None:
                            invalid_count += 1
                            if code == "first_value.blocked_unsafe_content":
                                blocked_count += 1
                            problem_counts[code] += 1
                            continue
                        valid_source_count += 1
                        ts = clean["ts"]
                        if not _in_window(ts, window_start_utc, window_end_utc):
                            outside_window_count += 1
                            continue
                        day = ts.date().isoformat()
                        active_dates.add(day)
                        event = str(clean["event"])
                        surface = str(clean["surface"])
                        event_counts[event] += 1
                        surface_counts[surface] += 1
                        client_tool = str(clean.get("client_tool") or "")
                        if client_tool:
                            client_tool_counts[client_tool] += 1
                        for field, value in clean["fields"].items():
                            event_block = field_value_counts.setdefault(event, {})
                            field_block = event_block.setdefault(str(field), Counter())
                            field_block[_field_value_label(value)] += 1
            except Exception:
                unreadable = True
                problem_counts["first_value.source_unreadable"] += 1

    valid_window_count = sum(event_counts.values())
    dates = sorted(active_dates)
    if dates:
        first_date = dates[0]
        last_date = dates[-1]
        observed_span_days = (
            date.fromisoformat(last_date) - date.fromisoformat(first_date)
        ).days + 1
    else:
        first_date = None
        last_date = None
        observed_span_days = 0

    field_values_out: dict[str, dict[str, dict[str, int]]] = {}
    for event in sorted(field_value_counts):
        field_values_out[event] = {}
        for field in sorted(field_value_counts[event]):
            field_values_out[event][field] = _sorted_counter(field_value_counts[event][field])

    return {
        "source_ref": "input:first_value_jsonl",
        "source_status": _source_status(
            provided=provided,
            exists=bool(exists),
            empty=not saw_line,
            valid=valid_source_count,
            invalid=invalid_count,
            unreadable=unreadable,
        ),
        "contributes_to_real_use": True,
        "contributes_to_readiness": True,
        "valid_record_count": valid_window_count,
        "source_valid_record_count": valid_source_count,
        "valid_records_outside_window_count": outside_window_count,
        "invalid_record_count": invalid_count,
        "blocked_record_count": blocked_count,
        "active_utc_days": len(dates),
        "active_utc_day_dates": dates,
        "observed_span_days": observed_span_days,
        "first_observed_utc_date": first_date,
        "last_observed_utc_date": last_date,
        "event_counts": _sorted_counter(event_counts),
        "surface_counts": _sorted_counter(surface_counts),
        "client_tool_counts": _sorted_counter(client_tool_counts),
        "field_value_counts": field_values_out,
        "deduplication_performed": False,
        "deduplication_reason": "first_value_events_jsonl_has_no_verifiable_event_id",
        "event_id_observed": False,
        "source_authenticity_verified": False,
        "append_only_integrity_verified": False,
        "coverage_semantics": (
            "owner-local closed-schema event observation; not independently "
            "verified user behavior"
        ),
        "problem_counts": _sorted_counter(problem_counts),
    }


def collect_operational_live_smoke(
    path: str | Path | None,
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
) -> dict[str, Any]:
    provided = path is not None and str(path) != ""
    exists = provided and Path(path).is_file()
    problem_counts: Counter[str] = Counter()
    outside_window_count = 0
    source_record_count = 0
    window_record_count = 0
    saw_line = False
    unreadable = False
    anchors = anchor_evidence._empty_anchor_counts()
    live_smoke = anchor_evidence._empty_live_smoke_counts()
    live_smoke.setdefault("failure_classes", {})
    live_smoke.setdefault("status_counts", {})

    if not provided or not exists:
        live_smoke["status_counts"]["missing"] = 1
        problem_counts["operational_live_smoke.source_missing"] += 1
    else:
        try:
            handle = Path(path).open("r", encoding="utf-8")
        except Exception:
            unreadable = True
            problem_counts["operational_live_smoke.source_unreadable"] += 1
        else:
            try:
                with handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        saw_line = True
                        source_record_count += 1
                        try:
                            record = json.loads(line)
                        except Exception:
                            problem_counts["operational_live_smoke.malformed_jsonl"] += 1
                            continue
                        if not isinstance(record, dict):
                            problem_counts["operational_live_smoke.non_object_json"] += 1
                            continue
                        timestamp = _parse_utc_timestamp(record.get("timestamp"))
                        if _contains_unsafe(record):
                            problem_counts["operational_live_smoke.blocked_unsafe_content"] += 1
                            if timestamp is not None:
                                if _in_window(timestamp, window_start_utc, window_end_utc):
                                    window_record_count += 1
                                    _record_unsafe_live_smoke_window_run(live_smoke)
                                else:
                                    outside_window_count += 1
                            continue
                        status, failure_code = anchor_evidence._valid_run_record(record)
                        if timestamp is None:
                            problem_counts[
                                f"operational_live_smoke.{failure_code or 'invalid_timestamp'}"
                            ] += 1
                            continue
                        if not _in_window(timestamp, window_start_utc, window_end_utc):
                            outside_window_count += 1
                            continue
                        window_record_count += 1
                        if failure_code is not None:
                            anchor_evidence._record_failed_run(live_smoke, status, failure_code)
                            problem_counts[f"operational_live_smoke.{failure_code}"] += 1
                            continue
                        live_smoke["status_counts"][status] = live_smoke["status_counts"].get(status, 0) + 1
                        live_smoke["runs"] = anchor_evidence._non_negative_int(live_smoke.get("runs", 0), 0) + 1
                        if status in {"stable", "downgrade"}:
                            live_smoke["passed"] = anchor_evidence._non_negative_int(live_smoke.get("passed", 0), 0) + 1
                            for key in anchor_evidence.ANCHOR_KEYS:
                                anchors[key] = (
                                    anchor_evidence._non_negative_int(anchors.get(key, 0), 0)
                                    + anchor_evidence._non_negative_int(record.get(key, 0), 0)
                                )
            except Exception:
                unreadable = True
                problem_counts["operational_live_smoke.source_unreadable"] += 1

    valid_passed = int(live_smoke.get("passed", 0) or 0)
    invalid_or_failed = int(live_smoke.get("failed", 0) or 0) + sum(
        count for code, count in problem_counts.items()
        if code.startswith("operational_live_smoke.") and code not in {
            "operational_live_smoke.source_missing",
            "operational_live_smoke.source_unreadable",
        }
    )
    return {
        "source_ref": "input:anchor_run_jsonl",
        "source_status": _source_status(
            provided=provided,
            exists=bool(exists),
            empty=not saw_line,
            valid=valid_passed,
            invalid=invalid_or_failed,
            unreadable=unreadable,
        ),
        "contributes_to_real_use": False,
        "runs": int(live_smoke.get("runs", 0) or 0),
        "passed": int(live_smoke.get("passed", 0) or 0),
        "failed": int(live_smoke.get("failed", 0) or 0),
        "status_counts": {
            key: int(live_smoke["status_counts"][key])
            for key in sorted(live_smoke.get("status_counts") or {})
        },
        "failure_classes": {
            key: int(live_smoke["failure_classes"][key])
            for key in sorted(live_smoke.get("failure_classes") or {})
        },
        "anchor_aggregate": {key: int(anchors.get(key, 0)) for key in sorted(anchors)},
        "source_record_count": source_record_count,
        "window_record_count": window_record_count,
        "records_outside_window_count": outside_window_count,
        "problem_counts": _sorted_counter(problem_counts),
        "health_semantics": "scheduled_task_and_anchor_health_not_user_outcome",
    }


def _validate_memory_eval_snapshot(snapshot: Any) -> tuple[dict[str, int] | None, bool, str]:
    if not isinstance(snapshot, dict):
        return None, False, "synthetic_memory_eval.non_object_json"
    if _contains_unsafe(snapshot):
        return None, False, "synthetic_memory_eval.blocked_unsafe_content"
    allowed = {"schema", "suite", "public_safe", "overall_passed", "recall", "admission", "agent_context_pack"}
    if set(snapshot) - allowed:
        return None, False, "synthetic_memory_eval.unknown_top_level_field"
    if snapshot.get("schema") != 1 or snapshot.get("suite") != "memory_eval_suite_v1":
        return None, False, "synthetic_memory_eval.invalid_schema"
    if snapshot.get("public_safe") is not True:
        return None, False, "synthetic_memory_eval.not_public_safe_aggregate"
    if not isinstance(snapshot.get("overall_passed"), bool):
        return None, False, "synthetic_memory_eval.invalid_overall_status"

    counts = {
        "recall_case_count": 0,
        "recall_passed_count": 0,
        "recall_failed_count": 0,
        "admission_candidate_count": 0,
        "admission_failed_expectation_count": 0,
        "agent_context_case_count": 0,
        "agent_context_passed_count": 0,
        "agent_context_failed_count": 0,
    }

    recall = snapshot.get("recall")
    admission = snapshot.get("admission")
    agent_context = snapshot.get("agent_context_pack")
    if not isinstance(recall, list) or not isinstance(admission, list) or not isinstance(agent_context, dict):
        return None, False, "synthetic_memory_eval.invalid_aggregate_shape"
    if not recall or not admission:
        return None, False, "synthetic_memory_eval.incomplete_suite"

    recall_keys = {
        "fixture",
        "benchmark",
        "public_safe",
        "overall_passed",
        "case_count",
        "passed_count",
        "failed_count",
        "mean_precision_at_k",
        "mean_recall_at_k",
        "mean_mrr",
        "forbidden_leak_rate",
        "negative_false_positive_rate",
    }
    for item in recall:
        if not isinstance(item, dict) or item.get("public_safe") is not True:
            return None, False, "synthetic_memory_eval.not_public_safe_aggregate"
        if set(item) - recall_keys:
            return None, False, "synthetic_memory_eval.unknown_nested_field"
        if not isinstance(item.get("overall_passed"), bool):
            return None, False, "synthetic_memory_eval.invalid_overall_status"
        case_count = _strict_positive_int(item.get("case_count"))
        passed_count = _strict_non_negative_int(item.get("passed_count"))
        failed_count = _strict_non_negative_int(item.get("failed_count"))
        if case_count is None or passed_count is None or failed_count is None:
            return None, False, "synthetic_memory_eval.incomplete_suite"
        if passed_count + failed_count != case_count:
            return None, False, "synthetic_memory_eval.inconsistent_counts"
        if bool(item.get("overall_passed")) and (failed_count != 0 or passed_count != case_count):
            return None, False, "synthetic_memory_eval.inconsistent_overall"
        counts["recall_case_count"] += case_count
        counts["recall_passed_count"] += passed_count
        counts["recall_failed_count"] += failed_count

    admission_keys = {
        "fixture",
        "guard",
        "public_safe",
        "overall_passed",
        "candidate_count",
        "failed_expectation_count",
        "action_counts",
    }
    for item in admission:
        if not isinstance(item, dict) or item.get("public_safe") is not True:
            return None, False, "synthetic_memory_eval.not_public_safe_aggregate"
        if set(item) - admission_keys:
            return None, False, "synthetic_memory_eval.unknown_nested_field"
        if not isinstance(item.get("overall_passed"), bool):
            return None, False, "synthetic_memory_eval.invalid_overall_status"
        candidate_count = _strict_positive_int(item.get("candidate_count"))
        failed_expectation_count = _strict_non_negative_int(item.get("failed_expectation_count"))
        if candidate_count is None or failed_expectation_count is None:
            return None, False, "synthetic_memory_eval.incomplete_suite"
        action_total, action_code = _strict_action_counts(item.get("action_counts"))
        if action_total is None:
            return None, False, action_code
        if action_total != candidate_count:
            return None, False, "synthetic_memory_eval.inconsistent_counts"
        if bool(item.get("overall_passed")) and failed_expectation_count != 0:
            return None, False, "synthetic_memory_eval.inconsistent_overall"
        counts["admission_candidate_count"] += candidate_count
        counts["admission_failed_expectation_count"] += failed_expectation_count

    agent_context_keys = {
        "schema",
        "public_safe",
        "store_isolated",
        "overall_passed",
        "case_count",
        "passed_count",
        "failed_count",
    }
    if set(agent_context) - agent_context_keys:
        return None, False, "synthetic_memory_eval.unknown_nested_field"
    if agent_context.get("schema") != "agent_context_pack_eval.v1":
        return None, False, "synthetic_memory_eval.invalid_schema"
    if agent_context.get("public_safe") is not True or agent_context.get("store_isolated") is not True:
        return None, False, "synthetic_memory_eval.not_store_isolated"
    if not isinstance(agent_context.get("overall_passed"), bool):
        return None, False, "synthetic_memory_eval.invalid_overall_status"
    case_count = _strict_positive_int(agent_context.get("case_count"))
    passed_count = _strict_non_negative_int(agent_context.get("passed_count"))
    failed_count = _strict_non_negative_int(agent_context.get("failed_count"))
    if case_count is None or passed_count is None or failed_count is None:
        return None, False, "synthetic_memory_eval.incomplete_suite"
    if passed_count + failed_count != case_count:
        return None, False, "synthetic_memory_eval.inconsistent_counts"
    if bool(agent_context.get("overall_passed")) and (
        failed_count != 0 or passed_count != case_count or case_count == 0
    ):
        return None, False, "synthetic_memory_eval.inconsistent_overall"
    counts["agent_context_case_count"] += case_count
    counts["agent_context_passed_count"] += passed_count
    counts["agent_context_failed_count"] += failed_count
    nested_overall = all(bool(item.get("overall_passed")) for item in recall)
    nested_overall = nested_overall and all(bool(item.get("overall_passed")) for item in admission)
    nested_overall = nested_overall and bool(agent_context.get("overall_passed"))
    if bool(snapshot.get("overall_passed")) != nested_overall:
        return None, False, "synthetic_memory_eval.inconsistent_overall"
    return counts, bool(snapshot.get("overall_passed")), ""


def collect_synthetic_memory_eval(paths: Iterable[str | Path] | None) -> dict[str, Any]:
    provided_paths = [Path(path) for path in (paths or []) if str(path) != ""]
    problem_counts: Counter[str] = Counter()
    aggregate_counts: Counter[str] = Counter()
    snapshot_count = 0
    passed_snapshot_count = 0
    failed_snapshot_count = 0
    invalid_snapshot_count = 0
    missing_snapshot_count = 0

    if not provided_paths:
        problem_counts["synthetic_memory_eval.source_missing"] += 1
        source_status = "source_missing"
    else:
        source_status = "no_valid_records"

    for path in provided_paths:
        if not path.is_file():
            missing_snapshot_count += 1
            problem_counts["synthetic_memory_eval.source_missing"] += 1
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            invalid_snapshot_count += 1
            problem_counts["synthetic_memory_eval.invalid_json"] += 1
            continue
        counts, passed, code = _validate_memory_eval_snapshot(loaded)
        if counts is None:
            invalid_snapshot_count += 1
            problem_counts[code] += 1
            continue
        snapshot_count += 1
        if passed:
            passed_snapshot_count += 1
        else:
            failed_snapshot_count += 1
        aggregate_counts.update(counts)

    if provided_paths:
        if snapshot_count > 0 and (invalid_snapshot_count > 0 or missing_snapshot_count > 0):
            source_status = "partial"
        elif snapshot_count > 0:
            source_status = "available"

    return {
        "source_ref": "input:memory_eval_json",
        "source_status": source_status,
        "contributes_to_real_use": False,
        "synthetic": True,
        "store_isolated_required": True,
        "snapshot_count": snapshot_count,
        "passed_snapshot_count": passed_snapshot_count,
        "failed_snapshot_count": failed_snapshot_count,
        "invalid_snapshot_count": invalid_snapshot_count,
        "missing_snapshot_count": missing_snapshot_count,
        "aggregate_case_counts": {
            key: int(aggregate_counts.get(key, 0))
            for key in (
                "recall_case_count",
                "recall_passed_count",
                "recall_failed_count",
                "admission_candidate_count",
                "admission_failed_expectation_count",
                "agent_context_case_count",
                "agent_context_passed_count",
                "agent_context_failed_count",
            )
        },
        "problem_counts": _sorted_counter(problem_counts),
        "health_semantics": "frozen_fixture_regression_not_real_use",
    }


def readiness_from_real_use(real_use: dict[str, Any]) -> dict[str, Any]:
    active_days = int(real_use.get("active_utc_days") or 0)
    observed_span = int(real_use.get("observed_span_days") or 0)
    if active_days < READINESS_THRESHOLDS["partial_min_active_utc_days"]:
        status = "insufficient"
        reason = "no_valid_real_use_first_value_utc_active_day_in_window"
    elif (
        active_days >= READINESS_THRESHOLDS["longitudinal_ready_min_active_utc_days"]
        and observed_span >= READINESS_THRESHOLDS["longitudinal_ready_min_observed_span_days"]
    ):
        status = "longitudinal_ready"
        reason = "real_use_first_value_meets_active_day_and_span_thresholds"
    else:
        status = "partial"
        reason = "some_real_use_first_value_observed_but_span_or_active_days_below_threshold"
    return {
        "status": status,
        "basis": "legal_real_use_first_value_utc_active_days_and_observed_span_only",
        "reason": reason,
        "thresholds": dict(READINESS_THRESHOLDS),
        "explanation": (
            "Evidence coverage readiness is not a product-value conclusion. "
            "Operational LIVE SMOKE and synthetic memory eval health are reported "
            "separately and never raise real-use readiness."
        ),
        "excluded_from_readiness": ["operational_live_smoke", "synthetic_memory_eval"],
    }


def build_evidence(
    *,
    first_value_jsonl: str | Path | None = None,
    anchor_run_jsonl: str | Path | None = None,
    memory_eval_jsons: Iterable[str | Path] | None = None,
    as_of: str,
    window_days: int,
) -> dict[str, Any]:
    window_start, window_end = window_bounds(as_of, window_days)
    real_use = collect_real_use_first_value(
        first_value_jsonl,
        window_start_utc=window_start,
        window_end_utc=window_end,
    )
    operational = collect_operational_live_smoke(
        anchor_run_jsonl,
        window_start_utc=window_start,
        window_end_utc=window_end,
    )
    synthetic = collect_synthetic_memory_eval(memory_eval_jsons)
    readiness = readiness_from_real_use(real_use)
    problems = _merge_problem_counts(
        real_use.get("problem_counts") or {},
        operational.get("problem_counts") or {},
        synthetic.get("problem_counts") or {},
    )
    return {
        "schema": SCHEMA,
        "version": 1,
        "evaluation_kind": EVALUATION_KIND,
        "private_internal": True,
        "owner_review_only": True,
        "public_summary_eligible": False,
        "as_of_utc": _format_utc(window_end),
        "window_start_utc": _format_utc(window_start),
        "window_end_utc": _format_utc(window_end),
        "window_days": int(window_days),
        "evidence_classes": {
            "real_use_first_value": real_use,
            "operational_live_smoke": operational,
            "synthetic_memory_eval": synthetic,
        },
        "coverage_readiness": readiness,
        "claim_boundary": {
            "supported": [
                "whether local opt-in first-value events were observed",
                "how many UTC days and what UTC date span those legal events cover",
                "whether scheduled LIVE SMOKE records were stable within the window",
                "whether frozen synthetic memory regression snapshots passed",
            ],
            "prohibited": [
                "unique_users",
                "retention",
                "causal_product_improvement",
                "production_success",
                "cross_user_generalization",
                "autonomous_learning_proven",
                "session_conversion",
                "user_success_rate",
                "independently_verified_real_use",
            ],
        },
        "problems": problems,
        "safety_flags": dict(SAFETY_FLAGS),
    }


def render_text(artifact: dict[str, Any]) -> str:
    evidence = artifact.get("evidence_classes") or {}
    real = evidence.get("real_use_first_value") or {}
    smoke = evidence.get("operational_live_smoke") or {}
    synthetic = evidence.get("synthetic_memory_eval") or {}
    readiness = artifact.get("coverage_readiness") or {}
    lines = [
        "Longitudinal real-use evidence (private owner-review artifact)",
        f"schema: {artifact.get('schema')}",
        f"as_of_utc: {artifact.get('as_of_utc')}",
        f"window_utc: {artifact.get('window_start_utc')} .. {artifact.get('window_end_utc')}",
        f"readiness: {readiness.get('status')} ({readiness.get('basis')})",
        (
            "real_use_first_value: "
            f"valid={real.get('valid_record_count')} "
            f"invalid={real.get('invalid_record_count')} "
            f"active_utc_days={real.get('active_utc_days')} "
            f"observed_span_days={real.get('observed_span_days')}"
        ),
        (
            "operational_live_smoke: "
            f"runs={smoke.get('runs')} passed={smoke.get('passed')} failed={smoke.get('failed')} "
            "contributes_to_real_use=false"
        ),
        (
            "synthetic_memory_eval: "
            f"snapshots={synthetic.get('snapshot_count')} "
            f"passed={synthetic.get('passed_snapshot_count')} "
            f"failed={synthetic.get('failed_snapshot_count')} "
            "contributes_to_real_use=false"
        ),
        "public_summary_eligible: false",
        "claim_boundary: no unique users, retention, production success, or user success rate.",
    ]
    return "\n".join(lines) + "\n"
