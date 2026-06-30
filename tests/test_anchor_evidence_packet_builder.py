from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_anchor_forum_evidence_packet.py"


def _evidence() -> dict[str, object]:
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
        "notes": ["Aggregate counts only."],
    }


def test_packet_builder_writes_local_review_packet(tmp_path: Path) -> None:
    evidence = tmp_path / "input.json"
    out_dir = tmp_path / "packet"
    evidence.write_text(json.dumps(_evidence()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(evidence),
            "--out-dir",
            str(out_dir),
            "--label",
            "weekend-dry-run",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "packet built" in result.stdout
    assert (out_dir / "anchor-live-smoke-evidence.json").exists()
    assert (out_dir / "anchor-live-smoke-metrics.md").exists()
    assert (out_dir / "cursor-forum-reply-draft.md").exists()
    assert (out_dir / "manifest.json").exists()

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = (out_dir / "anchor-live-smoke-metrics.md").read_text(encoding="utf-8")
    draft = (out_dir / "cursor-forum-reply-draft.md").read_text(encoding="utf-8")
    body = json.dumps(manifest, ensure_ascii=False) + metrics + draft

    assert manifest["schema"] == "anchor_forum_evidence_packet.v1"
    assert manifest["label"] == "weekend-dry-run"
    assert manifest["public_action"] is False
    assert manifest["owner_confirmation_required"] is True
    assert manifest["files"]["evidence"] == "anchor-live-smoke-evidence.json"
    assert "12 anchor checks" in metrics
    assert "7 LIVE_SMOKE runs" in metrics
    assert "Owner confirmation required before posting" in draft
    assert "Draft reply:" in draft
    assert "Caveats:" in draft
    assert "not a statistically significant result" in draft
    assert str(tmp_path) not in body
    assert "Workspace With Spaces" not in body
    assert "secret.json" not in body


def test_packet_builder_builds_from_synthetic_collector(tmp_path: Path) -> None:
    out_dir = tmp_path / "packet"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--synthetic",
            "--out-dir",
            str(out_dir),
            "--label",
            "synthetic-review",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    evidence = json.loads((out_dir / "anchor-live-smoke-evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert evidence["mode"] == "synthetic"
    assert manifest["source_mode"] == "synthetic"


def test_packet_builder_passes_aggregate_input_files_to_collector(tmp_path: Path) -> None:
    anchor_json = tmp_path / "anchor.json"
    live_smoke_json = tmp_path / "live-smoke.json"
    out_dir = tmp_path / "packet"
    anchor_json.write_text(json.dumps({
        "anchors": {
            "checked": 12,
            "valid": 9,
            "invalid": 1,
            "unknown": 2,
            "superseded": 1,
            "demoted_to_staging": 1,
        },
    }), encoding="utf-8")
    live_smoke_json.write_text(json.dumps({
        "live_smoke": {
            "runs": 7,
            "passed": 6,
            "failed": 1,
            "failure_classes": {"timeout": 1},
        },
    }), encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--synthetic",
            "--anchor-json",
            str(anchor_json),
            "--live-smoke-json",
            str(live_smoke_json),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads((out_dir / "anchor-live-smoke-evidence.json").read_text(encoding="utf-8"))

    assert evidence["anchors"]["checked"] == 12
    assert evidence["live_smoke"]["runs"] == 7
    assert evidence["live_smoke"]["failure_classes"] == {"timeout": 1}


def test_packet_builder_rejects_private_aggregate_input_before_collection(tmp_path: Path) -> None:
    anchor_json = tmp_path / "anchor.json"
    out_dir = tmp_path / "packet"
    anchor_json.write_text(json.dumps({
        "anchors": {"checked": 1, "valid": 1},
        "raw_memory": "PRIVATE_LOCAL_MARKER Workspace With Spaces secret.json",
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--synthetic",
            "--anchor-json",
            str(anchor_json),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr

    assert result.returncode == 1
    assert "aggregate input contains private-looking content" in result.stderr
    assert "Workspace With Spaces" not in body
    assert "secret.json" not in body
    assert not (out_dir / "cursor-forum-reply-draft.md").exists()


def test_packet_builder_refuses_invalid_evidence_without_writing_draft(tmp_path: Path) -> None:
    evidence = tmp_path / "input.json"
    out_dir = tmp_path / "packet"
    payload = _evidence()
    payload["public_safe"] = False
    evidence.write_text(json.dumps(payload), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--out-dir", str(out_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "validation failed" in result.stderr
    assert not (out_dir / "cursor-forum-reply-draft.md").exists()


def test_packet_builder_reports_invalid_json_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "input.json"
    out_dir = tmp_path / "packet"
    evidence.write_text("{not json", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--evidence", str(evidence), "--out-dir", str(out_dir)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "evidence file must be valid JSON" in result.stderr
    assert not (out_dir / "cursor-forum-reply-draft.md").exists()


def test_packet_builder_live_mode_requires_owner_flag(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--live",
            "--out-dir",
            str(tmp_path / "packet"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "--allow-live" in result.stderr


def test_packet_builder_live_mode_with_owner_flag_builds_aggregate_packet(tmp_path: Path) -> None:
    out_dir = tmp_path / "packet"

    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--live",
            "--allow-live",
            "--out-dir",
            str(out_dir),
            "--label",
            "live-review",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    evidence = json.loads((out_dir / "anchor-live-smoke-evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    metrics = (out_dir / "anchor-live-smoke-metrics.md").read_text(encoding="utf-8")

    assert evidence["mode"] == "live"
    assert manifest["source_mode"] == "live"
    assert manifest["public_action"] is False
    assert manifest["owner_confirmation_required"] is True
    assert "Validation warnings:" in metrics
    assert "small sample size; avoid statistical claims" in metrics
