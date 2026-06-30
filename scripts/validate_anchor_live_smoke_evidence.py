"""Validate public-safe Anchor/LIVE_SMOKE aggregate evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA = "anchor_live_smoke_evidence.v1"
REPORT_SCHEMA = "anchor_live_smoke_validation.v1"
TOP_LEVEL_KEYS = {
    "schema",
    "date",
    "public_safe",
    "mode",
    "anchors",
    "live_smoke",
    "notes",
}
ANCHOR_KEYS = {
    "checked",
    "valid",
    "invalid",
    "unknown",
    "superseded",
    "demoted_to_staging",
}
LIVE_SMOKE_KEYS = {"runs", "passed", "failed", "failure_classes"}
PRIVATE_TOKENS = (
    "raw_memory",
    "PRIVATE_LOCAL_MARKER",
    "PRIVATE_DEBUG_MARKER",
    "Workspace With Spaces",
    "secret.json",
    "debug.log",
    "E:\\",
    "C:\\",
    "/Users/",
)
PRIVATE_REGEXES = (
    re.compile(r"(?i)\bapi[_-]?key\b"),
    re.compile(r"(?i)\bpassword\b"),
    re.compile(r"(?i)\btoken\b"),
    re.compile(r"(?i)\bprivate[_-]?key\b"),
    re.compile(r"(?i)(^|[\\/])\.ssh([\\/]|$)"),
)


def _load(path_text: str) -> tuple[dict[str, Any], list[str]]:
    path = Path(path_text)
    if not path.exists():
        return {}, ["evidence file not found"]
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, ["evidence file must be valid JSON"]
    if not isinstance(loaded, dict):
        return {}, ["evidence must be a JSON object"]
    return loaded, []


def _count(block: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(block.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def _private_string(text: str) -> bool:
    return any(token in text for token in PRIVATE_TOKENS) or any(
        pattern.search(text) for pattern in PRIVATE_REGEXES
    )


def contains_private_content(value: Any) -> bool:
    if isinstance(value, str):
        return _private_string(value)
    if isinstance(value, dict):
        return any(
            _private_string(str(key)) or contains_private_content(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_private_content(item) for item in value)
    return False


def _is_safe_failure_class(label: str) -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")
    return 1 <= len(label) <= 64 and all(char in allowed for char in label)


def validation_warnings(payload: dict[str, Any]) -> list[str]:
    anchors = payload.get("anchors") if isinstance(payload.get("anchors"), dict) else {}
    live_smoke = payload.get("live_smoke") if isinstance(payload.get("live_smoke"), dict) else {}
    warnings: list[str] = []
    if _count(anchors, "checked") < 5 or _count(live_smoke, "runs") < 3:
        warnings.append("small sample size; avoid statistical claims")
    return warnings


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        errors.append("unexpected evidence schema")
    if payload.get("public_safe") is not True:
        errors.append("evidence must declare public_safe true")

    unknown_top_level = set(payload) - TOP_LEVEL_KEYS
    if unknown_top_level:
        errors.append("unknown top-level fields are not allowed")

    if contains_private_content(payload):
        errors.append("private-looking content detected")

    anchors = payload.get("anchors")
    if not isinstance(anchors, dict):
        errors.append("anchors must be an object")
        anchors = {}
    live_smoke = payload.get("live_smoke")
    if not isinstance(live_smoke, dict):
        errors.append("live_smoke must be an object")
        live_smoke = {}

    for key in ANCHOR_KEYS:
        value = anchors.get(key, 0)
        if not isinstance(value, int) or value < 0:
            errors.append(f"anchors.{key} must be a non-negative integer")
    for key in ("runs", "passed", "failed"):
        value = live_smoke.get(key, 0)
        if not isinstance(value, int) or value < 0:
            errors.append(f"live_smoke.{key} must be a non-negative integer")

    checked = _count(anchors, "checked")
    status_total = _count(anchors, "valid") + _count(anchors, "invalid") + _count(anchors, "unknown")
    if status_total > checked:
        errors.append("anchor status counts exceed checked")

    runs = _count(live_smoke, "runs")
    passed_failed = _count(live_smoke, "passed") + _count(live_smoke, "failed")
    if passed_failed != runs:
        errors.append("LIVE_SMOKE passed plus failed must equal runs")

    failures = live_smoke.get("failure_classes", {})
    if not isinstance(failures, dict):
        errors.append("live_smoke.failure_classes must be an object")
    else:
        for label, value in failures.items():
            if not isinstance(label, str) or not _is_safe_failure_class(label):
                errors.append("unsafe failure class label")
                break
            if not isinstance(value, int) or value < 0:
                errors.append("failure class counts must be non-negative integers")
                break

    unknown_anchor_keys = set(anchors) - ANCHOR_KEYS
    unknown_live_keys = set(live_smoke) - LIVE_SMOKE_KEYS
    if unknown_anchor_keys:
        errors.append("unknown anchor fields are not allowed")
    if unknown_live_keys:
        errors.append("unknown live_smoke fields are not allowed")
    return errors


def report_for(payload: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    warnings = [] if errors else validation_warnings(payload)
    return {
        "schema": REPORT_SCHEMA,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "public_action": False,
        "owner_confirmation_required": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, help="Aggregate evidence JSON file.")
    parser.add_argument("--json", action="store_true", help="Print JSON report.")
    args = parser.parse_args()

    payload, load_errors = _load(args.evidence)
    errors = load_errors or validate_payload(payload)
    report = report_for(payload, errors)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif errors:
        print("Anchor/LIVE_SMOKE evidence validation failed")
        for error in errors:
            print(f"- {error}")
    else:
        print("Anchor/LIVE_SMOKE evidence validation passed")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
