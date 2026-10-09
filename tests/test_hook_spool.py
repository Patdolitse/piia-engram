"""Offline hook reliability; all stores and child profiles are synthetic."""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import portalocker
import pytest


SUMMARY = "Remember to validate path containment before reading transcripts because it prevents accidental disclosure."


def child_env(base, store):
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CLAUDE_CONFIG_DIR"):
        directory = base / key.lower()
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env.update(ENGRAM_DIR=str(store), DO_NOT_TRACK="1", ENGRAM_NO_UPDATE_CHECK="1",
               PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    for key in ("CLAUDE_INVOKED_BY", "ENGRAM_CURSOR_INJECT_ACTIVE",
                "ENGRAM_CURSOR_SAVE_ACTIVE", "ENGRAM_CURSOR_WRITEBACK_ACTIVE"):
        env.pop(key, None)
    return env


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("module", ["auto_save_on_stop", "auto_absorb_compact",
                                   "cursor_save_on_stop", "cursor_writeback"])
def test_write_hook_ignores_main_store_lock(tmp_path, module):
    store = tmp_path / "store"
    (store / "knowledge").mkdir(parents=True)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text((json.dumps({"content": SUMMARY * 3}) + "\n") * 12,
                          encoding="utf-8")
    env = child_env(tmp_path / "profile", store)
    env["ENGRAM_CURSOR_WRITEBACK"] = "1"
    with portalocker.Lock(store / "knowledge" / ".engram-write.lock", "a", timeout=0):
        start = time.monotonic()
        result = subprocess.run([sys.executable, "-m", "piia_engram.hooks." + module],
                                input=json.dumps({"summary": SUMMARY, "cwd": str(tmp_path),
                                                  "transcript_path": str(transcript)}),
                                text=True, capture_output=True, env=env, timeout=3)
        assert result.returncode == 0, result.stderr
        assert time.monotonic() - start < 3
    assert list((store / "hooks" / "spool").glob("*.jsonl"))
    assert not (store / "identity").exists()
    assert not (store / "contexts").exists()


@pytest.mark.parametrize("module", ["auto_save_on_stop", "auto_absorb_compact",
                                   "cursor_save_on_stop", "cursor_writeback"])
def test_unreadable_store_returns_zero_and_logs(tmp_path, module):
    store = tmp_path / "not-a-directory"
    store.write_text("unreadable store root", encoding="utf-8")
    env = child_env(tmp_path / "profile", store)
    env["ENGRAM_CURSOR_WRITEBACK"] = "1"
    result = subprocess.run([sys.executable, "-m", "piia_engram.hooks." + module],
                            input=json.dumps({"transcript_path": "unavailable.jsonl", "summary": SUMMARY}),
                            text=True, capture_output=True,
                            env=env, timeout=3)
    assert result.returncode == 0
    assert list(tmp_path.glob("*.hooks.log"))


def test_atomic_concurrent_publish(tmp_path):
    from piia_engram.hooks.spool import backlog
    store = tmp_path / "store"
    code = "from piia_engram.hooks.spool import enqueue; [enqueue('cursor_writeback', 'cursor', {'summary': 'test'}) for _ in range(15)]"
    children = [subprocess.Popen([sys.executable, "-c", code],
                                env=child_env(tmp_path / f"profile-{n}", store))
                for n in range(6)]
    assert all(child.wait(timeout=15) == 0 for child in children)
    files = list((store / "hooks" / "spool").glob("*.jsonl"))
    assert len(files) == 90
    events = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    assert all(len(p.read_text(encoding="utf-8").splitlines()) == 1 for p in files)
    assert len({e["event_id"] for e in events}) == 90
    assert backlog(store)["pending"] == 90


def test_drain_duplicate_event_once(tmp_path):
    from piia_engram.core import Engram
    from piia_engram.hooks.spool import enqueue, drain, spool_dir
    event = enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    original = next(spool_dir(tmp_path).glob("*.jsonl")).read_bytes()
    (spool_dir(tmp_path) / "replay.jsonl").write_bytes(original)
    assert drain(tmp_path)["processed"] == 1
    assert drain(tmp_path)["processed"] == 0
    lessons = Engram(root=tmp_path, read_only=True).get_lessons(limit=None, _update_access=False)
    assert len(lessons) == 1
    assert lessons[0]["tier"] == "staging"
    assert lessons[0]["hook_event_id"] == event
    (spool_dir(tmp_path) / "later-replay.jsonl").write_bytes(original)
    assert drain(tmp_path)["duplicates"] == 1
    assert len(Engram(root=tmp_path, read_only=True).get_lessons(limit=None, _update_access=False)) == 1


def test_poison_quarantined_not_deleted(tmp_path):
    from piia_engram.hooks.spool import drain, spool_dir, backlog
    directory = spool_dir(tmp_path)
    directory.mkdir(parents=True)
    (directory / "bad.jsonl").write_text("broken JSON\n", encoding="utf-8")
    assert drain(tmp_path)["quarantined"] == 1
    assert backlog(tmp_path)["quarantined"] == 1
    assert (directory / "quarantine" / "bad.jsonl").read_text() == "broken JSON\n"


def test_cap_quarantines_oldest(tmp_path, monkeypatch):
    from piia_engram.hooks import spool
    monkeypatch.setattr(spool, "MAX_PENDING_EVENTS", 2)
    ids = [spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
           for _ in range(3)]
    assert spool.backlog(tmp_path)["pending"] == 2
    quarantined = list((spool.spool_dir(tmp_path) / "quarantine").glob("*.jsonl"))
    assert len(quarantined) == 1
    assert json.loads(quarantined[0].read_text())["event_id"] == ids[0]


@pytest.mark.parametrize("error", [OSError("disk full"), RuntimeError("store locked")])
def test_offline_failure_keeps_event(tmp_path, monkeypatch, error):
    from piia_engram.hooks import spool
    spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    from piia_engram import core
    real = core.Engram
    monkeypatch.setattr(core, "Engram", lambda **kwargs: (_ for _ in ()).throw(error))
    assert spool.drain(tmp_path)["failed"] == 1
    assert spool.backlog(tmp_path)["pending"] == 1
    monkeypatch.setattr(core, "Engram", real)
    assert spool.drain(tmp_path)["processed"] == 1


def test_dry_run_and_doctor_are_read_only(tmp_path, capsys, monkeypatch):
    from piia_engram.hooks.spool import enqueue, drain, backlog
    from piia_engram import setup_wizard  # historical doctor re-export initializes first
    from piia_engram.doctor import run_doctor_json
    enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    before = snapshot(tmp_path)
    report = drain(tmp_path, dry_run=True)
    assert report["pending"] == 1
    assert backlog(tmp_path)["oldest_age_seconds"] >= 0
    assert run_doctor_json() == 0
    data = json.loads(capsys.readouterr().out)
    assert data["hook_spool"]["pending"] == 1
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("module", ["auto_inject_resume_brief", "cursor_inject_resume_brief"])
def test_session_start_strict_budget(tmp_path, monkeypatch, capsys, module):
    from importlib import import_module
    from piia_engram import core
    from piia_engram.hooks import _budget
    monkeypatch.setattr(_budget, "READ_BUDGET_SECONDS", 0.05)
    class SlowEngram:
        def __init__(self, *, read_only):
            assert read_only is True
        def get_resume_brief(self, **kwargs):
            time.sleep(0.5)
            return {"markdown": "late"}
    monkeypatch.setattr(core, "Engram", SlowEngram)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(sys, "argv", ["hook"])
    start = time.monotonic()
    import_module("piia_engram.hooks." + module).main()
    assert time.monotonic() - start < 0.25
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_mcp_import_does_not_drain(tmp_path):
    from piia_engram.hooks.spool import enqueue, spool_dir
    enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    files = {p.name: p.read_bytes() for p in spool_dir(tmp_path).glob("*.jsonl")}
    result = subprocess.run([sys.executable, "-c", "import piia_engram.mcp_server"],
                            env=child_env(tmp_path / "profile", tmp_path),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_bytes() for p in spool_dir(tmp_path).glob("*.jsonl")} == files
    assert not (tmp_path / "knowledge" / "lessons.json").exists()


def test_staging_extraction_drains_but_mcp_does_not(tmp_path):
    from piia_engram.core import Engram
    from piia_engram.hooks.spool import enqueue, backlog
    from piia_engram.write_provenance import origin_scope, ORIGIN_MCP
    eng = Engram(root=tmp_path)
    enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    with origin_scope(ORIGIN_MCP):
        eng.extract_session_insights("", force_staging=True)
    assert backlog(tmp_path)["pending"] == 1
    eng.extract_session_insights("", force_staging=True)
    assert backlog(tmp_path)["pending"] == 0


def test_partial_commit_replay_keeps_one_staging_row(tmp_path, monkeypatch):
    from piia_engram.core import Engram
    from piia_engram.hooks import spool
    from piia_engram import context
    original_metadata = context._make_extraction_metadata
    attempts = []

    def changing_metadata(*args, **kwargs):
        attempts.append(True)
        return dict(original_metadata(*args, **kwargs), attempt_nonce=len(attempts))

    monkeypatch.setattr(context, "_make_extraction_metadata", changing_metadata)
    event_id = spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    original = Engram.add_lesson

    def interrupted(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("interrupted after persisted candidate")

    monkeypatch.setattr(Engram, "add_lesson", interrupted)
    assert spool.drain(tmp_path)["failed"] == 1
    # A changed candidate must not cause the event to re-extract altered input.
    eng = Engram(root=tmp_path, read_only=True)
    rows = eng.get_lessons(limit=None, _update_access=False)
    assert len(rows) == 1
    Engram(root=tmp_path).update_lesson(rows[0]["id"], {
        "summary": "A distinct owner-edited proposal that must survive replay."})
    monkeypatch.setattr(Engram, "add_lesson", original)
    assert spool.drain(tmp_path)["processed"] == 1
    rows = eng.get_lessons(limit=None, _update_access=False)
    assert len(rows) == 1 and rows[0]["hook_event_id"] == event_id
    assert rows[0]["summary"] == "A distinct owner-edited proposal that must survive replay."


@pytest.mark.parametrize("kind", ["cursor_save", "claude_compact"])
def test_receipt_failure_retries_archive_once(tmp_path, monkeypatch, kind):
    from piia_engram.hooks import spool
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"content": "A" * 300}) + "\n", encoding="utf-8")
    data = {"summary": SUMMARY, "session_id": "synthetic-session"} if kind == "cursor_save" else {
        "transcript_path": str(transcript)}
    spool.enqueue(kind, "cursor" if kind == "cursor_save" else "claude_code", data, root=tmp_path)
    original = spool._publish

    def receipt_failure(path, value):
        if path.parent.name == "receipts":
            raise OSError("disk full at receipt")
        return original(path, value)

    monkeypatch.setattr(spool, "_publish", receipt_failure)
    assert spool.drain(tmp_path)["failed"] == 1
    transcript.write_text(json.dumps({"content": "B" * 300}) + "\n", encoding="utf-8")
    monkeypatch.setattr(spool, "_publish", original)
    assert spool.drain(tmp_path)["processed"] == 1
    area = tmp_path / ("contexts" if kind == "cursor_save" else "daily")
    files = list(area.rglob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert text.count("<!-- hook-event:") == 1
    if kind == "claude_compact":
        assert "A" * 300 in text and "B" * 300 not in text


def test_byte_cap_and_poison_dry_run(tmp_path, monkeypatch):
    from piia_engram.hooks import spool
    monkeypatch.setattr(spool, "MAX_PENDING_BYTES", 600)
    spool.enqueue("cursor_writeback", "cursor", {"summary": "x" * 300}, root=tmp_path)
    spool.enqueue("cursor_writeback", "cursor", {"summary": "y" * 300}, root=tmp_path)
    assert spool.backlog(tmp_path)["pending_bytes"] <= 600
    assert spool.backlog(tmp_path)["quarantined"] == 1
    directory = spool.spool_dir(tmp_path)
    (directory / "poison.jsonl").write_text("bad json", encoding="utf-8")
    before = snapshot(tmp_path)
    spool.drain(tmp_path, dry_run=True)
    assert snapshot(tmp_path) == before


def test_processor_busy_does_not_block_producer(tmp_path):
    from piia_engram.hooks import spool
    directory = spool.spool_dir(tmp_path)
    directory.mkdir(parents=True)
    with spool._lock(directory):
        start = time.monotonic()
        assert spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
        assert time.monotonic() - start < 0.25
        assert spool.drain(tmp_path)["busy"]
    assert spool.drain(tmp_path)["processed"] == 1


def test_cli_dry_run_skips_notices_and_telemetry(tmp_path, monkeypatch, capsys):
    from piia_engram import setup_wizard as wizard
    from piia_engram.hooks.spool import enqueue
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    enqueue("cursor_writeback", "cursor", {"summary": SUMMARY})
    before = snapshot(tmp_path)
    monkeypatch.setattr(sys, "argv", ["engram", "hooks", "drain", "--dry-run", "--json"])
    monkeypatch.setattr(wizard, "_start_usage_ping_cli", lambda: pytest.fail("unexpected telemetry"))
    with pytest.raises(SystemExit) as result:
        wizard.main()
    assert result.value.code == 0
    assert json.loads(capsys.readouterr().out)["pending"] == 1
    assert snapshot(tmp_path) == before


def test_doctor_missing_store_does_not_create_it(tmp_path, monkeypatch, capsys):
    from piia_engram import setup_wizard
    from piia_engram.doctor import run_doctor_json
    store = tmp_path / "absent"
    monkeypatch.setenv("ENGRAM_DIR", str(store))
    assert run_doctor_json() == 0
    assert json.loads(capsys.readouterr().out)["hook_spool"]["pending"] == 0
    assert not store.exists()


def test_retry_does_not_reset_event_age(tmp_path, monkeypatch):
    from piia_engram.hooks import spool
    from piia_engram import core
    spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=tmp_path)
    path = next(spool.spool_dir(tmp_path).glob("*.jsonl"))
    event = json.loads(path.read_text(encoding="utf-8"))
    event["created_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    monkeypatch.setattr(core, "Engram", lambda **kwargs: (_ for _ in ()).throw(OSError("offline")))
    assert spool.drain(tmp_path)["failed"] == 1
    assert spool.backlog(tmp_path)["oldest_age_seconds"] > 365 * 86400


@pytest.mark.parametrize("module", ["auto_inject_resume_brief", "cursor_inject_resume_brief"])
def test_read_timeout_process_exits_without_joining_worker(tmp_path, module):
    code = """
import builtins, io, os, sys, time, threading
from importlib import import_module
from pathlib import Path
from piia_engram import core
from piia_engram.hooks import _budget
from piia_engram.hooks._log import log_failure
_budget.READ_BUDGET_SECONDS = 0.05
def delayed_background_log(*args, **kwargs):
    # A scheduled daemon cannot be relied on to write before process exit.
    if threading.current_thread() is not threading.main_thread():
        time.sleep(30)
    log_failure(*args, **kwargs)
_budget.log_failure = delayed_background_log
original_print = builtins.print
def checked_print(*args, **kwargs):
    log = (Path(os.environ['ENGRAM_DIR']) / 'logs' / 'hooks.log').read_text(encoding='utf-8')
    assert 'resume read budget exceeded' in log and log.endswith(chr(10))
    original_print(*args, **kwargs)
builtins.print = checked_print
class Slow:
    def __init__(self, **kwargs): pass
    def get_resume_brief(self, **kwargs):
        time.sleep(30)
core.Engram = Slow
sys.stdin = io.StringIO('{}')
raise SystemExit(import_module('piia_engram.hooks.' + sys.argv[1]).main())
"""
    # Allow cold imports on busy Windows hosts, while still rejecting a join
    # of either 30-second worker. The application budget remains 50 ms.
    result = subprocess.run([sys.executable, "-c", code, module], text=True, capture_output=True,
                            env=child_env(tmp_path / "profile", tmp_path / "store"), timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"continue": True}
    log = (tmp_path / "store" / "logs" / "hooks.log").read_text(encoding="utf-8")
    assert f"[{module}] resume read budget exceeded" in log


@pytest.mark.parametrize("module", ["auto_inject_resume_brief", "cursor_inject_resume_brief"])
def test_read_timeout_log_write_failure_still_exits_zero(tmp_path, module):
    code = """
import builtins, io, sys, time
from importlib import import_module
from pathlib import Path
from piia_engram import core
from piia_engram.hooks import _budget
_budget.READ_BUDGET_SECONDS = 0.05
class Slow:
    def __init__(self, **kwargs): pass
    def get_resume_brief(self, **kwargs):
        time.sleep(30)
core.Engram = Slow
original_open, original_path_open = builtins.open, Path.open
def denied_open(file, *args, **kwargs):
    if str(file).endswith('hooks.log'):
        raise OSError('log unavailable')
    return original_open(file, *args, **kwargs)
def denied_path_open(file, *args, **kwargs):
    if str(file).endswith('hooks.log'):
        raise OSError('log unavailable')
    return original_path_open(file, *args, **kwargs)
builtins.open, Path.open = denied_open, denied_path_open
sys.stdin = io.StringIO('{}')
raise SystemExit(import_module('piia_engram.hooks.' + sys.argv[1]).main())
"""
    result = subprocess.run([sys.executable, "-c", code, module], text=True, capture_output=True,
                            env=child_env(tmp_path / "profile", tmp_path / "store"), timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"continue": True}
    assert result.stderr == ""


def test_maximum_unicode_summary_does_not_double_envelope_size(tmp_path):
    from piia_engram.hooks import spool
    assert spool.enqueue("cursor_writeback", "cursor", {"summary": "🧠" * 20_000}, root=tmp_path)
    result = spool.drain(tmp_path)
    assert result["processed"] == 1
    assert result["failed"] == result["quarantined"] == 0


def test_mcp_stdio_initialize_and_read_leave_queue_untouched(tmp_path):
    """Real JSON-RPC using ordinary pipes, including environments without overlapped pipes."""
    import queue
    import threading
    from piia_engram.hooks.spool import enqueue, spool_dir
    store = tmp_path / "store"
    enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=store)
    before = {p.name: p.read_bytes() for p in spool_dir(store).glob("*.jsonl")}
    env = child_env(tmp_path / "profile", store)
    env.update(ENGRAM_EPHEMERAL="1", ENGRAM_TOOLS="core")
    process = subprocess.Popen([sys.executable, "-m", "piia_engram.mcp_server"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env)
    responses = queue.Queue()

    def read_frames():
        for line in process.stdout:
            try:
                responses.put(json.loads(line))
            except ValueError:
                continue

    reader = threading.Thread(target=read_frames, daemon=True)
    reader.start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def response(request_id):
        deadline = time.monotonic() + 20
        while True:
            frame = responses.get(timeout=max(0.01, deadline - time.monotonic()))
            if frame.get("id") == request_id:
                assert "error" not in frame, frame
                return frame["result"]

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "synthetic-test", "version": "1"}}})
        assert response(1)["serverInfo"]
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert any(tool["name"] == "get_user_context" for tool in response(2)["tools"])
        send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "get_user_context", "arguments": {"level": "quick"}}})
        assert not response(3).get("isError")
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
        process.stdout.close()
    assert {p.name: p.read_bytes() for p in spool_dir(store).glob("*.jsonl")} == before
    assert not (store / "knowledge" / "lessons.json").exists()
