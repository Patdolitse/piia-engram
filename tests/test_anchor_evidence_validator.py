from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_anchor_live_smoke_evidence.py"


def _valid_payload() -> dict[str, object]:
    return {
        "schema": "anchor_live_smoke_evidence.v1",
        "date": "2026-06-30",
        "public_safe": True,
        "mode": "manual",
        "anchors": {
            "checked": 12,
            "valid": 9,
            "invalid": 1,
            "unknown": 2,
            "superseded": 1,
            "demoted_to_staging": 1,
        },
        "live_smoke": {
            "runs": 7,
            "passed": 6,
            "failed": 1,
            "failure_classes": {"timeout": 1},
        },
        "notes": [
            "Aggregate counts only.",
            "No raw memory bodies, local paths, or private identifiers.",
        ],
    }


def test_validator_accepts_public_safe_aggregate_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_valid_payload()), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "anchor_live_smoke_validation.v1"
    assert payload["valid"] is True
    assert payload["errors"] == []
    assert payload["warnings"] == []


def test_validator_rejects_count_invariants(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["anchors"]["checked"] = 2  # type: ignore[index]
    payload["live_smoke"]["runs"] = 5  # type: ignore[index]
    payload["live_smoke"]["passed"] = 4  # type: ignore[index]
    payload["live_smoke"]["failed"] = 4  # type: ignore[index]
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert report["valid"] is False
    assert "anchor status counts exceed checked" in report["errors"]
    assert "LIVE_SMOKE passed plus failed must equal runs" in report["errors"]


def test_validator_rejects_private_tokens_without_echoing_them(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["raw_memory"] = "private body"
    payload["source"] = "PRIVATE_LOCAL_MARKER Workspace With Spaces secret.json"
    payload["credential_hint"] = "api_key=abc123"
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "private-looking content detected" in report["errors"]
    assert "Workspace With Spaces" not in body
    assert "secret.json" not in body


def test_validator_rejects_broad_private_path_shapes_without_echoing(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["notes"] = [
        "Aggregate counts only.",
        "Source artifacts stayed under Z:\\internal\\audit.log",
        "Mirror output used \\\\server\\private-share\\artifact.json",
        "Linux runner copied /home/alice/private/output.json",
        "Temp runner copied /tmp/private-output.json",
    ]
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "private-looking content detected" in report["errors"]
    assert "Z:\\internal" not in body
    assert "\\\\server\\private-share" not in body
    assert "/home/alice" not in body


def test_validator_rejects_private_project_codename_marker(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["notes"] = [
        "Aggregate counts only.",
        "PRIVATE_PROJECT_CODENAME_MARKER must never be part of forum evidence.",
    ]
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "private-looking content detected" in report["errors"]


def test_validator_rejects_unsafe_failure_class_labels(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["live_smoke"]["failure_classes"] = {  # type: ignore[index]
        "timeout": 1,
        "PRIVATE_DEBUG_MARKER Workspace With Spaces debug.log": 1,
    }
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "unsafe failure class label" in report["errors"]


def test_validator_rejects_negative_counts(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["anchors"]["checked"] = -1  # type: ignore[index]
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "anchors.checked must be a non-negative integer" in report["errors"]


def test_validator_rejects_bool_counts(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["live_smoke"]["runs"] = True  # type: ignore[index]
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "live_smoke.runs must be a non-negative integer" in report["errors"]


def test_validator_rejects_inconsistent_status_counts(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["live_smoke"]["status_counts"] = {  # type: ignore[index]
        "stable": 4,
        "downgrade": 0,
        "failed": 0,
        "parse_failed": 2,
    }
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "LIVE_SMOKE stable plus downgrade status counts must equal passed" in report["errors"]
    assert "LIVE_SMOKE failed plus parse_failed status counts must equal failed" in report["errors"]
    assert "LIVE_SMOKE status counts must equal runs excluding missing" in report["errors"]


def test_validator_allows_consistent_status_counts_with_missing_outside_runs(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["live_smoke"]["status_counts"] = {  # type: ignore[index]
        "stable": 6,
        "failed": 1,
        "missing": 1,
    }
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["valid"] is True


def test_validator_rejects_unknown_top_level_fields(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["manual_note"] = "safe-looking but not part of the public schema"
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert result.returncode == 1
    assert "unknown top-level fields are not allowed" in report["errors"]


def test_validator_warns_on_small_sample_without_rejecting(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["anchors"]["checked"] = 1  # type: ignore[index]
    payload["anchors"]["valid"] = 1  # type: ignore[index]
    payload["anchors"]["invalid"] = 0  # type: ignore[index]
    payload["anchors"]["unknown"] = 0  # type: ignore[index]
    payload["live_smoke"]["runs"] = 1  # type: ignore[index]
    payload["live_smoke"]["passed"] = 1  # type: ignore[index]
    payload["live_smoke"]["failed"] = 0  # type: ignore[index]
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)

    assert report["valid"] is True
    assert "small sample size; avoid statistical claims" in report["warnings"]
