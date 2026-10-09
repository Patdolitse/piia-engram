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


def test_unreadable_store_returns_zero_and_logs(tmp_path):
    store = tmp_path / "not-a-directory"
    store.write_text("unreadable store root", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "piia_engram.hooks.auto_save_on_stop"],
                            input=json.dumps({"transcript_path": "unavailable.jsonl"}),
                            text=True, capture_output=True,
                            env=child_env(tmp_path / "profile", store), timeout=3)
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
