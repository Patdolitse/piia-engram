"""Metadata-only capture diagnostics and retention, using synthetic stores."""
import json
import os
import time

import pytest

from piia_engram.hooks import spool


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def test_absent_diagnostics_and_dry_run_never_create_store(tmp_path):
    root = tmp_path / "absent"
    result = spool.backlog(root)
    assert result["receipts"] == result["quarantined_bytes"] == result["partial_bytes"] == 0
    assert result["host_consumption"] == "unknown"
    assert result["store"]["id"]
    assert spool.drain(root, dry_run=True)["read_only"]
    assert not root.exists()


def test_metadata_covers_all_spool_files_without_disclosing_body(tmp_path):
    root = tmp_path / "store"
    spool.enqueue("cursor_writeback", "cursor", {"summary": "BODY_MARKER"}, root=root)
    directory = spool.spool_dir(root)
    quarantine = directory / "quarantine"
    receipts = directory / "receipts"
    quarantine.mkdir()
    receipts.mkdir()
    (quarantine / "old.jsonl").write_text("BODY_MARKER", encoding="utf-8")
    (quarantine / "old.reason.json").write_text('{"reason":"invalid-event"}', encoding="utf-8")
    (quarantine / "unexpected.reason.json").write_text('{"reason":"BODY_MARKER"}', encoding="utf-8")
    partial = receipts / "event.json.test.partial"
    partial.write_text("BODY_MARKER", encoding="utf-8")
    (receipts / "event.json").write_text('{"event_id":"sample","processed_at":"2020-01-01T00:00:00Z"}', encoding="utf-8")
    stamp = time.time() - 8 * 86400
    for path in (partial, quarantine / "old.jsonl"):
        os.utime(path, (stamp, stamp))
    before = snapshot(root)
    result = spool.backlog(root)
    assert result["pending"] == result["quarantined"] == result["partial"] == result["receipts"] == 1
    assert result["quarantined_bytes"] >= len("BODY_MARKER")
    assert result["partial_bytes"] == len("BODY_MARKER")
    assert result["receipt_bytes"] > 0
    assert result["cleanup_candidates"]["quarantine"]["count"] == 1
    assert result["cleanup_candidates"]["partial"]["count"] == 1
    assert result["cleanup_candidates"]["receipts"]["count"] == 0
    assert "invalid-event" in json.dumps(result["recent_results"])
    assert "unknown" in json.dumps(result["recent_results"])
    assert "BODY_MARKER" not in json.dumps(result)
    assert "old.jsonl" not in json.dumps(result)
    assert str(root) not in json.dumps(result)
    assert snapshot(root) == before


def test_old_backlog_shows_existing_drain_hint_without_processing(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    class OffsetOnlyDatetime:
        @staticmethod
        def fromisoformat(value):
            # Exercise the supported Python 3.10 parser on newer interpreters too.
            if value.endswith("Z"):
                raise ValueError("UTC suffix requires an explicit offset")
            return datetime.fromisoformat(value)

    monkeypatch.setattr(spool, "datetime", OffsetOnlyDatetime)
    directory = spool.spool_dir(tmp_path)
    directory.mkdir(parents=True)
    (directory / "event.jsonl").write_text('{"created_at":"2001-01-01T00:00:00Z","payload":{"summary":"BODY_MARKER"}}', encoding="utf-8")
    before = snapshot(tmp_path)
    result = spool.backlog(tmp_path)
    expected_age = time.time() - datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp()
    assert result["oldest_age_seconds"] == pytest.approx(expected_age, abs=2)
    assert "engram hooks drain" in result["drain_hint"]
    assert result["states"]["queued"] != result["states"]["processed"]
    assert "approved" in result["states"]["processed"]
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("error,code", [(ValueError("BODY_MARKER"), "processing-failed"),
    (OSError(28, "BODY_MARKER"), "storage-unavailable"),
    (FileNotFoundError("BODY_MARKER"), "transcript-missing")])
def test_recent_failures_use_closed_codes_in_existing_event_metadata(tmp_path, monkeypatch, error, code):
    from piia_engram.hooks import _processor
    spool.enqueue("cursor_writeback", "cursor", {"summary": "BODY_MARKER"}, root=tmp_path)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(_processor, "prepare", fail)
    spool.drain(tmp_path)
    result = spool.backlog(tmp_path)
    assert code in json.dumps(result["recent_results"])
    assert "BODY_MARKER" not in json.dumps(result)


def test_success_receipt_is_not_approval_or_host_confirmation(tmp_path):
    spool.enqueue("cursor_writeback", "cursor", {"summary": "Validate the sample format before processing because it avoids mismatched inputs."}, root=tmp_path)
    assert spool.drain(tmp_path)["processed"] == 1
    result = spool.backlog(tmp_path)
    assert result["receipts"] == 1
    assert "processed" in json.dumps(result["recent_results"])
    assert result["host_consumption"] == "unknown"
    assert "not approved" in result["states"]["processed"]


def test_doctor_json_and_text_show_metadata_and_same_store_without_writes(tmp_path, monkeypatch, capsys):
    from piia_engram import doctor
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    spool.enqueue("cursor_writeback", "cursor", {"summary": "BODY_MARKER"}, root=root)
    before = snapshot(root)
    assert doctor.run_doctor_json() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["hook_spool"]["store"] == spool.backlog(root)["store"]
    doctor._print_connection_report(root)
    text = capsys.readouterr().out
    assert "Host consumption: unknown" in text
    assert report["hook_spool"]["store"]["id"] in text
    assert "receipts kept for dedup" in text
    assert "BODY_MARKER" not in text
    assert "BODY_MARKER" not in json.dumps(report)
    assert snapshot(root) == before


def test_receipt_failure_and_poison_keep_evidence_with_closed_codes(tmp_path, monkeypatch):
    spool.enqueue("cursor_writeback", "cursor", {"summary": "Validate sample inputs before processing because shape mismatch causes failures."}, root=tmp_path)
    publish = spool._publish
    def fail_receipt(path, data):
        if path.parent.name == "receipts":
            raise OSError("BODY_MARKER")
        publish(path, data)
    monkeypatch.setattr(spool, "_publish", fail_receipt)
    assert spool.drain(tmp_path)["failed"] == 1
    assert "receipt-failed" in json.dumps(spool.backlog(tmp_path)["recent_results"])
    assert spool.backlog(tmp_path)["pending"] == 1
    monkeypatch.setattr(spool, "_publish", publish)
    assert spool.drain(tmp_path)["processed"] == 1
    (spool.spool_dir(tmp_path) / "bad.jsonl").write_text("BODY_MARKER", encoding="utf-8")
    assert spool.drain(tmp_path)["quarantined"] == 1
    assert "invalid-event" in json.dumps(spool.backlog(tmp_path)["recent_results"])
    assert "BODY_MARKER" not in json.dumps(spool.backlog(tmp_path))
