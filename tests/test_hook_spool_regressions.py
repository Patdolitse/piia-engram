"""Deferred capture failures, schema gates and stable checkpoint provenance."""
import builtins
import errno
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import portalocker
import pytest

from test_hook_spool import SUMMARY, child_env


def queued(root, kind, payload, prepared=None):
    from piia_engram.hooks import spool
    event_id = spool.enqueue(kind, spool.KINDS[kind], payload, root=root)
    path = next(p for p in spool.spool_dir(root).glob("*.jsonl") if event_id in p.name)
    if prepared is not None:
        event = json.loads(path.read_text(encoding="utf-8"))
        event["prepared"] = prepared
        path.write_text(json.dumps(event), encoding="utf-8")
    return event_id, path


@pytest.mark.parametrize("kind", ["cursor_writeback", "claude_compact"])
def test_transcript_read_failure_is_retryable(tmp_path, monkeypatch, kind):
    from piia_engram.hooks import spool
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({"content": SUMMARY * 3}) + "\n", encoding="utf-8")
    event_id, path = queued(tmp_path, kind, {
        "transcript_path": str(transcript), "roots": [str(tmp_path)]})
    original_open = builtins.open
    original_path_open = Path.open

    def deny_open(file, *args, **kwargs):
        if Path(file) == transcript:
            raise PermissionError("temporary read failure")
        return original_open(file, *args, **kwargs)

    reads = []

    def deny_path_open(file, *args, **kwargs):
        if file == transcript:
            reads.append(True)
            # Permit the old compact readability probe, then fail the actual read.
            if kind != "claude_compact" or len(reads) > 1 or args[:1] != ("rb",):
                raise PermissionError("temporary read failure")
        return original_path_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", deny_open)
    monkeypatch.setattr(Path, "open", deny_path_open)
    result = spool.drain(tmp_path)
    assert result["processed"] == 0
    assert result["failed"] == result["pending"] == 1
    assert result["quarantined"] == 0
    assert path.exists()
    assert not (path.parent / "receipts" / (event_id + ".json")).exists()
    monkeypatch.setattr(builtins, "open", original_open)
    monkeypatch.setattr(Path, "open", original_path_open)
    assert spool.drain(tmp_path)["processed"] == 1


def test_missing_transcript_does_not_block_and_exhausts_retries(tmp_path):
    from piia_engram.hooks import spool
    event_id, missing = queued(tmp_path, "claude_stop", {
        "transcript_path": str(tmp_path / "missing.jsonl")})
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY})
    result = spool.drain(tmp_path)
    assert result["processed"] == 1
    assert result["failed"] == result["pending"] == 1
    assert missing.exists()
    assert spool.drain(tmp_path)["pending"] == 1
    result = spool.drain(tmp_path)
    assert result["quarantined"] == 1 and result["pending"] == 0
    quarantine = missing.parent / "quarantine"
    assert (quarantine / missing.name).exists()
    reason = json.loads((quarantine / missing.with_suffix(".reason.json").name).read_text())
    assert reason["reason"] == "transcript-missing-retry-limit"
    assert not (missing.parent / "receipts" / (event_id + ".json")).exists()


def test_old_missing_transcript_is_quarantined_with_reason(tmp_path):
    from piia_engram.hooks import spool
    _, path = queued(tmp_path, "claude_compact", {"transcript_path": str(tmp_path / "missing.jsonl")})
    event = json.loads(path.read_text(encoding="utf-8"))
    event["created_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(event), encoding="utf-8")
    result = spool.drain(tmp_path)
    assert result["quarantined"] == 1 and result["pending"] == 0
    reason = path.parent / "quarantine" / path.with_suffix(".reason.json").name
    assert json.loads(reason.read_text())["reason"] == "transcript-missing-age-limit"


@pytest.mark.parametrize("error", [portalocker.LockException("store busy"),
                                       OSError(errno.ENOSPC, "disk full")])
def test_shared_storage_failure_stops_batch(tmp_path, monkeypatch, error):
    from piia_engram.hooks import spool, _processor
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY})
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY + " Another entry."})
    attempted = []

    def fail(event, *args):
        attempted.append(event["event_id"])
        raise error

    monkeypatch.setattr(_processor, "process", fail)
    result = spool.drain(tmp_path)
    assert result["failed"] == 1 and result["pending"] == 2
    assert len(attempted) == 1


@pytest.mark.parametrize("module", ["auto_save_on_stop", "auto_absorb_compact",
                                   "cursor_save_on_stop", "cursor_writeback"])
def test_write_input_deadline_with_open_pipe(tmp_path, module):
    env = child_env(tmp_path / "profile", tmp_path / "store")
    env["ENGRAM_CURSOR_WRITEBACK"] = "1"
    process = subprocess.Popen([sys.executable, "-m", "piia_engram.hooks." + module],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env)
    start = time.monotonic()
    try:
        process.stdin.write(b'{"summary":')
        process.stdin.flush()
        # Deliberately retain the open pipe: EOF cannot release the reader.
        assert process.wait(timeout=2.5) == 0
        assert time.monotonic() - start < 2.5
        log = tmp_path / "store" / "logs" / "hooks.log"
        assert "TimeoutError" in log.read_text(encoding="utf-8")
        assert not list((tmp_path / "store" / "hooks" / "spool").glob("*.jsonl"))
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()


def test_write_input_size_limit(tmp_path):
    from piia_engram.hooks.spool import MAX_EVENT_BYTES
    data = json.dumps({"summary": SUMMARY, "unused": "x" * (MAX_EVENT_BYTES * 2)})
    result = subprocess.run([sys.executable, "-m", "piia_engram.hooks.cursor_writeback"],
                            input=data, text=True, capture_output=True, timeout=3,
                            env=dict(child_env(tmp_path / "profile", tmp_path / "store"),
                                     ENGRAM_CURSOR_WRITEBACK="1"))
    assert result.returncode == 0
    assert not list((tmp_path / "store" / "hooks" / "spool").glob("*.jsonl"))
    assert "ValueError" in (tmp_path / "store" / "logs" / "hooks.log").read_text()


@pytest.mark.parametrize("kind,payload,prepared", [
    ("claude_stop", {}, None),
    ("claude_compact", {"transcript_path": ""}, None),
    ("cursor_writeback", {}, None),
    ("claude_compact", {"transcript_path": "missing.jsonl"}, {}),
    ("claude_stop", {"transcript_path": "missing.jsonl"}, {"summary": "text"}),
    ("cursor_save", {}, {"session_id": "id"}),
    ("cursor_writeback", {"summary": SUMMARY}, {"skip": False}),
    ("claude_compact", {"transcript_path": "missing.jsonl"}, {"skip": True, "summary": 42}),
])
def test_required_event_fields_are_quarantined_before_processing(tmp_path, monkeypatch,
                                                               kind, payload, prepared):
    from piia_engram.hooks import spool, _processor
    _, bad = queued(tmp_path, kind, payload, prepared)
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY})
    original_process = _processor.process

    def check(event, *args):
        assert event["event_id"] not in bad.name, "poison reached processing"
        return original_process(event, *args)

    monkeypatch.setattr(_processor, "process", check)
    result = spool.drain(tmp_path)
    assert result["quarantined"] == result["processed"] == 1
    assert result["failed"] == result["pending"] == 0
    assert (bad.parent / "quarantine" / bad.name).exists()


@pytest.mark.parametrize("kind", ["claude_stop", "claude_compact", "cursor_save", "cursor_writeback"])
def test_explicit_prepared_skip_is_valid(tmp_path, kind):
    from piia_engram.hooks import spool
    queued(tmp_path, kind, {"transcript_path": "unavailable.jsonl"}, {"skip": True})
    result = spool.drain(tmp_path)
    assert result["processed"] == 1
    assert result["failed"] == result["quarantined"] == 0
    assert not (tmp_path / "knowledge").exists()


def test_deferred_digest_revision_is_current_and_stable_on_replay(tmp_path, monkeypatch):
    from piia_engram.core import Engram
    from piia_engram.hooks import spool
    eng = Engram(root=tmp_path / "store")
    project = str(tmp_path / "project")
    checkpoint = {"current_state": {"next_actions": ["verify checkpoint"]},
                  "checkpoint": {"revision": 7, "generated_at": "2026-10-01T00:00:00Z"}}
    monkeypatch.setattr(eng, "get_project_snapshot", lambda folder: checkpoint)
    eng.save_agent_context("cursor", "Next: verify checkpoint", session_id="baseline", project_folder=project)
    baseline = eng.get_session_digest("cursor", "baseline")
    assert baseline["source"]["project_revision"] == 7
    _, path = queued(eng.root, "cursor_save", {
        "summary": "Next: verify checkpoint", "session_id": "deferred", "project_folder": project})
    original = spool._publish

    def no_receipt(target, data):
        if target.parent.name == "receipts":
            raise OSError(errno.ENOSPC, "receipt unavailable")
        return original(target, data)

    monkeypatch.setattr(spool, "_publish", no_receipt)
    assert spool.drain(eng.root, engram=eng)["failed"] == 1
    digest = eng.get_session_digest("cursor", "deferred")
    arbitration = eng._project_handoff_from_sources(project_folder=project,
                                                  snapshot=checkpoint, digests=[digest])
    assert arbitration["freshness"]["status"] == "current"
    assert digest["source"]["project_revision"] == baseline["source"]["project_revision"]
    assert digest["source"]["project_revision_capture"] == "deferred_prepare"
    assert datetime.fromisoformat(digest["source"]["project_revision_captured_at"]).tzinfo is not None
    event = json.loads(path.read_text(encoding="utf-8"))
    assert digest["generated_at"] == event["created_at"]
    assert digest["source"]["project_revision_captured_at"] == event["prepared"]["project_revision_captured_at"]
    digest_path = eng._session_digest_path("cursor", "deferred")
    original_bytes = digest_path.read_bytes()
    assert path.exists()
    checkpoint["checkpoint"]["revision"] = 8
    monkeypatch.setattr(spool, "_publish", original)
    assert spool.drain(eng.root, engram=eng)["processed"] == 1
    assert digest_path.read_bytes() == original_bytes
    assert eng.get_session_digest("cursor", "deferred")["source"]["project_revision"] == 7


def test_event_processing_error_does_not_block_later_events(tmp_path, monkeypatch):
    from piia_engram.hooks import spool, _processor
    event_id, path = queued(tmp_path, "cursor_writeback", {"summary": SUMMARY})
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY + " Healthy event."})
    original = _processor.process

    def fail_one(event, *args):
        if event["event_id"] == event_id:
            raise RuntimeError("event-specific failure")
        return original(event, *args)

    monkeypatch.setattr(_processor, "process", fail_one)
    result = spool.drain(tmp_path)
    assert result["failed"] == result["processed"] == result["pending"] == 1
    assert path.exists()
    assert not (path.parent / "receipts" / (event_id + ".json")).exists()


def test_preparation_schema_error_is_quarantined_before_store_writes(tmp_path, monkeypatch):
    from piia_engram.hooks import spool, _processor
    _, path = queued(tmp_path, "cursor_save", {})
    monkeypatch.setattr(_processor, "prepare", lambda *args: {})
    monkeypatch.setattr(_processor, "process", lambda *args: pytest.fail("invalid preparation reached writer"))
    result = spool.drain(tmp_path)
    assert result["quarantined"] == 1
    assert result["failed"] == result["processed"] == 0
    assert (path.parent / "quarantine" / path.name).exists()


def test_wrapped_store_lock_stops_batch(tmp_path, monkeypatch):
    from piia_engram.hooks import spool, _processor
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY})
    queued(tmp_path, "cursor_writeback", {"summary": SUMMARY})
    attempts = []

    def fail(event, *args):
        attempts.append(event["event_id"])
        try:
            raise portalocker.LockException("store lock")
        except portalocker.LockException as exc:
            raise RuntimeError("storage lock wrapper") from exc

    monkeypatch.setattr(_processor, "process", fail)
    result = spool.drain(tmp_path)
    assert result["failed"] == 1 and result["pending"] == 2
    assert len(attempts) == 1
