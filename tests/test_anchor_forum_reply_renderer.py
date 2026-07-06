from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_anchor_forum_reply.py"


def test_reply_renderer_outputs_owner_review_draft(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "schema": "anchor_live_smoke_evidence.v1",
        "public_safe": True,
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
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    text = result.stdout

    assert "Owner confirmation required before posting" in text
    assert "12 anchor checks" in text
    assert "7 LIVE_SMOKE runs" in text
    assert "raw data" in text
    assert "local dataset" in text
    assert "not a statistically significant result" in text


def test_reply_renderer_redacts_input_paths_and_raw_fields(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "schema": "anchor_live_smoke_evidence.v1",
        "public_safe": True,
        "anchors": {
            "checked": 1,
            "valid": 1,
            "invalid": 0,
            "unknown": 0,
            "superseded": 0,
            "demoted_to_staging": 0,
        },
        "live_smoke": {"runs": 1, "passed": 1, "failed": 0, "failure_classes": {}},
        "raw_memory": "private memory body",
        "path": "PRIVATE_LOCAL_MARKER Workspace With Spaces secret.json",
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    text = result.stdout

    assert "Workspace With Spaces" not in text
    assert "secret.json" not in text
    assert "private memory body" not in text


def test_reply_renderer_refuses_missing_evidence_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(missing)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "evidence file not found" in result.stderr


def test_reply_renderer_refuses_invalid_json(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{not json", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "evidence file must be valid JSON" in result.stderr


def test_reply_renderer_refuses_non_public_safe_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "schema": "anchor_live_smoke_evidence.v1",
        "public_safe": False,
        "anchors": {},
        "live_smoke": {},
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "evidence must be public-safe aggregate JSON" in result.stderr


def test_reply_renderer_includes_anchor_trust_boundaries(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "schema": "anchor_live_smoke_evidence.v1",
        "public_safe": True,
        "anchors": {
            "checked": 2,
            "valid": 1,
            "invalid": 0,
            "unknown": 1,
            "superseded": 0,
            "demoted_to_staging": 0,
        },
        "live_smoke": {"runs": 1, "passed": 1, "failed": 0, "failure_classes": {}},
    }), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    text = result.stdout

    assert "controlled harness plus a small historical replay" in text
    assert "unknown is not the same as false" in text
    assert "becoming reachable again does not automatically make the claim trusted" in text
    assert "checks structural evidence, not semantic truth" in text
