from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_anchor_live_smoke_evidence.py"


def test_collector_outputs_public_safe_aggregate_schema() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--synthetic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "anchor_live_smoke_evidence.v1"
    assert payload["public_safe"] is True
    assert {"checked", "valid", "invalid", "unknown", "superseded"} <= set(payload["anchors"])
    assert {"runs", "passed", "failed"} <= set(payload["live_smoke"])


def test_collector_does_not_emit_raw_paths_or_memory_bodies() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--synthetic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    body = result.stdout

    assert "E:\\" not in body
    assert "C:\\" not in body
    assert "/Users/" not in body
    assert "raw_memory" not in body
    assert "secret" not in body.lower()


def test_live_aggregate_mode_requires_owner_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--live"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--allow-live" in result.stderr


def test_live_aggregate_mode_runs_isolated_live_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--live", "--allow-live"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["mode"] == "live"
    assert payload["live_smoke"]["runs"] >= 1
    assert payload["live_smoke"]["passed"] + payload["live_smoke"]["failed"] == payload["live_smoke"]["runs"]


def test_collector_merges_anchor_and_live_smoke_json_inputs(tmp_path: Path) -> None:
    anchor_json = tmp_path / "anchor.json"
    live_smoke_json = tmp_path / "live-smoke.json"
    anchor_json.write_text(json.dumps({
        "anchors": {
            "checked": 12,
            "valid": 9,
            "invalid": 1,
            "unknown": 2,
            "superseded": 1,
            "demoted_to_staging": 1,
        },
        "raw_memory": "must not appear",
        "local_path": "PRIVATE_LOCAL_MARKER Workspace With Spaces secret.json",
    }), encoding="utf-8")
    live_smoke_json.write_text(json.dumps({
        "live_smoke": {
            "runs": 7,
            "passed": 6,
            "failed": 1,
            "failure_classes": {"timeout": 1},
        },
        "debug_log": "PRIVATE_DEBUG_MARKER Workspace With Spaces debug.log",
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--synthetic",
            "--anchor-json",
            str(anchor_json),
            "--live-smoke-json",
            str(live_smoke_json),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    body = result.stdout

    assert payload["anchors"]["checked"] == 12
    assert payload["anchors"]["valid"] == 9
    assert payload["live_smoke"]["runs"] == 7
    assert payload["live_smoke"]["failure_classes"] == {"timeout": 1}
    assert "raw_memory" not in body
    assert "Workspace With Spaces" not in body
    assert "debug.log" not in body


def test_collector_can_write_public_safe_output_file(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "evidence.json"
    anchor_json = tmp_path / "anchor.json"
    live_smoke_json = tmp_path / "live-smoke.json"
    anchor_json.write_text(json.dumps({
        "anchors": {"checked": 1, "valid": 1},
        "raw_memory": "private memory body",
    }), encoding="utf-8")
    live_smoke_json.write_text(json.dumps({
        "live_smoke": {"runs": 1, "passed": 1},
        "debug_log": "PRIVATE_DEBUG_MARKER Workspace With Spaces debug.log",
    }), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--synthetic",
            "--anchor-json",
            str(anchor_json),
            "--live-smoke-json",
            str(live_smoke_json),
            "--out",
            str(out),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    body = out.read_text(encoding="utf-8")
    payload = json.loads(body)

    assert payload["schema"] == "anchor_live_smoke_evidence.v1"
    assert payload["public_safe"] is True
    assert "private memory body" not in body
    assert "Workspace With Spaces" not in body
    assert "debug.log" not in body


def test_collector_sanitizes_failure_class_labels_from_inputs(tmp_path: Path) -> None:
    live_smoke_json = tmp_path / "live-smoke.json"
    live_smoke_json.write_text(json.dumps({
        "live_smoke": {
            "runs": 3,
            "passed": 1,
            "failed": 2,
            "failure_classes": {
                "timeout": 1,
                "PRIVATE_DEBUG_MARKER Workspace With Spaces debug.log": 1,
            },
        },
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--synthetic",
            "--live-smoke-json",
            str(live_smoke_json),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["live_smoke"]["failure_classes"] == {"timeout": 1, "other": 1}
    assert "Workspace With Spaces" not in result.stdout
    assert "debug.log" not in result.stdout


def test_collector_explains_zero_anchor_checks_without_forging_counts(tmp_path: Path) -> None:
    anchor_json = tmp_path / "anchor.json"
    anchor_json.write_text(json.dumps({
        "anchors": {
            "checked": 0,
            "valid": 0,
            "invalid": 0,
            "unknown": 0,
            "superseded": 0,
            "demoted_to_staging": 0,
        },
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--synthetic",
            "--anchor-json",
            str(anchor_json),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["anchors"]["checked"] == 0
    assert payload["anchors"]["valid"] == 0
    assert any(
        "current live store has no structured anchor records" in note
        for note in payload["notes"]
    )
    assert "daily log" not in result.stdout.lower()
