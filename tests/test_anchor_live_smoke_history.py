from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "append_anchor_live_smoke_history.py"


def _evidence(
    *,
    date: str,
    checked: int,
    valid: int = 0,
    invalid: int = 0,
    unknown: int = 0,
    runs: int = 1,
    passed: int = 1,
    failed: int = 0,
    status_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "schema": "anchor_live_smoke_evidence.v1",
        "date": date,
        "public_safe": True,
        "mode": "live",
        "anchors": {
            "checked": checked,
            "valid": valid,
            "invalid": invalid,
            "unknown": unknown,
            "superseded": 0,
            "demoted_to_staging": invalid,
        },
        "live_smoke": {
            "runs": runs,
            "passed": passed,
            "failed": failed,
            "failure_classes": {"timeout": failed} if failed else {},
            "status_counts": status_counts or {},
        },
        "notes": ["Aggregate counts only."],
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_append(history_dir: Path, evidence: Path, collected_at: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(evidence),
            "--history-dir",
            str(history_dir),
            "--collected-at",
            collected_at,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_history_append_does_not_overwrite_existing_entries(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    _write_json(first, _evidence(date="2026-07-05", checked=0))
    _write_json(second, _evidence(date="2026-07-06", checked=2, valid=1, unknown=1))

    _run_append(history_dir, first, "2026-07-05T10:00:00Z")
    _run_append(history_dir, second, "2026-07-06T10:00:00Z")

    lines = (history_dir / "anchor-live-smoke-history.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]

    assert len(entries) == 2
    assert entries[0]["collected_at"] == "2026-07-05T10:00:00Z"
    assert entries[1]["collected_at"] == "2026-07-06T10:00:00Z"
    assert entries[0]["anchors"]["checked"] == 0
    assert entries[1]["anchors"]["checked"] == 2


def test_history_summary_aggregates_recent_7_and_14_day_windows(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    old_evidence = tmp_path / "old.json"
    recent_evidence = tmp_path / "recent.json"
    _write_json(old_evidence, _evidence(date="2026-06-25", checked=9, valid=9, runs=2, passed=2))
    _write_json(recent_evidence, _evidence(date="2026-07-06", checked=3, valid=2, invalid=1, runs=1, passed=0, failed=1))

    _run_append(history_dir, old_evidence, "2026-06-25T10:00:00Z")
    _run_append(history_dir, recent_evidence, "2026-07-06T10:00:00Z")

    summary = json.loads((history_dir / "latest.json").read_text(encoding="utf-8"))
    seven = summary["windows"]["7d"]["evidence"]
    fourteen = summary["windows"]["14d"]["evidence"]
    summary_md = (history_dir / "summary.md").read_text(encoding="utf-8")

    assert seven["anchors"]["checked"] == 3
    assert seven["live_smoke"]["runs"] == 1
    assert seven["live_smoke"]["failure_classes"] == {"timeout": 1}
    assert fourteen["anchors"]["checked"] == 12
    assert fourteen["live_smoke"]["runs"] == 3
    assert "Last 7 days" in summary_md
    assert "Last 14 days" in summary_md


def test_history_preserves_live_smoke_status_counts(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    evidence = tmp_path / "evidence.json"
    _write_json(
        evidence,
        _evidence(
            date="2026-07-06",
            checked=3,
            valid=3,
            runs=3,
            passed=1,
            failed=2,
            status_counts={"stable": 1, "failed": 1, "parse_failed": 1},
        ),
    )

    _run_append(history_dir, evidence, "2026-07-06T10:00:00Z")

    entry = json.loads((history_dir / "anchor-live-smoke-history.jsonl").read_text(encoding="utf-8"))
    latest = json.loads((history_dir / "latest.json").read_text(encoding="utf-8"))

    assert entry["live_smoke"]["status_counts"] == {"stable": 1, "failed": 1, "parse_failed": 1}
    assert latest["windows"]["7d"]["evidence"]["live_smoke"]["status_counts"] == {
        "stable": 1,
        "failed": 1,
        "parse_failed": 1,
    }


def test_history_rejects_private_paths_tokens_and_transcripts_without_echoing(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    private_evidence = tmp_path / "private.json"
    payload = _evidence(date="2026-07-06", checked=1, valid=1)
    payload["raw_transcript"] = "PRIVATE_TRANSCRIPT_MARKER should be blocked"
    payload["credential_hint"] = "token=abc123"
    payload["local_path"] = "E:\\Private\\debug.log"
    _write_json(private_evidence, payload)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(private_evidence),
            "--history-dir",
            str(history_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr

    assert result.returncode == 1
    assert "private-looking content detected" in result.stderr
    assert "PRIVATE_TRANSCRIPT_MARKER" not in body
    assert "token=abc123" not in body
    assert "E:\\Private" not in body
    assert not (history_dir / "anchor-live-smoke-history.jsonl").exists()


def test_history_records_zero_anchor_reason_without_forging_counts(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    zero_evidence = tmp_path / "zero.json"
    _write_json(zero_evidence, _evidence(date="2026-07-06", checked=0))

    _run_append(history_dir, zero_evidence, "2026-07-06T10:00:00Z")

    entry = json.loads((history_dir / "anchor-live-smoke-history.jsonl").read_text(encoding="utf-8"))
    latest = json.loads((history_dir / "latest.json").read_text(encoding="utf-8"))

    assert entry["anchors"]["checked"] == 0
    assert entry["anchor_zero_reason"] == "current live store has no structured anchor records"
    assert latest["windows"]["7d"]["evidence"]["anchors"]["checked"] == 0
    assert any(
        "current live store has no structured anchor records" in note
        for note in latest["windows"]["7d"]["evidence"]["notes"]
    )


def test_packet_builder_can_build_from_history_summary(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    evidence = tmp_path / "evidence.json"
    out_dir = tmp_path / "packet"
    _write_json(evidence, _evidence(date="2026-07-06", checked=4, valid=3, unknown=1, runs=2, passed=2))
    _run_append(history_dir, evidence, "2026-07-06T10:00:00Z")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build_anchor_forum_evidence_packet.py"),
            "--history-summary",
            str(history_dir / "latest.json"),
            "--history-window-days",
            "7",
            "--out-dir",
            str(out_dir),
            "--label",
            "history-7d-review",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    packet_evidence = json.loads((out_dir / "anchor-live-smoke-evidence.json").read_text(encoding="utf-8"))
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert "packet built" in result.stdout
    assert packet_evidence["mode"] == "history_7d"
    assert packet_evidence["anchors"]["checked"] == 4
    assert manifest["source_mode"] == "history-summary-7d"


def test_local_evidence_directory_is_gitignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".engram-local-evidence/" in gitignore
