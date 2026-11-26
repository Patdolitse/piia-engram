"""The hook queue is processed without anyone running ``engram hooks drain``.

Hooks only queue events. Nothing processed the queue unless the owner ran the
drain command, so session records piled up and, once their transcript was
gone, could no longer be read. The MCP server now processes the queue from a
background thread and the watcher after each scan: a few events at a time,
never as an AI client's write, and only into the review queue.

Every store is a temporary one. Every thread started here is stopped and
joined by its test.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from piia_engram import Engram
from piia_engram.hooks import autodrain, spool
from piia_engram.write_provenance import ORIGIN_MCP, origin_scope

SUMMARY = ("Remember to validate path containment before reading transcripts "
           "because it prevents accidental disclosure.")


def _queue(root, count=1):
    return [spool.enqueue("cursor_writeback", "cursor", {"summary": f"{SUMMARY} Case {n}."}, root=root)
            for n in range(count)]


def _pending(root):
    return len(list(spool.spool_dir(root).glob("*.jsonl")))


def _rows(root, name):
    path = root / "knowledge" / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _trusted(root):
    from piia_engram import recall_policy

    return [row["id"] for name in ("lessons.json", "decisions.json")
            for row in _rows(root, name) if recall_policy.is_trusted(row)]


@pytest.fixture(autouse=True)
def fresh_process_memory(monkeypatch):
    """What a process remembers between passes starts empty in every test."""
    monkeypatch.setattr(autodrain, "_failed_at", {}, raising=False)
    monkeypatch.setattr(autodrain, "_last_watcher_pass", None, raising=False)
    monkeypatch.setattr(autodrain, "_watcher_retry_after", None, raising=False)


def _fail_marked_events(monkeypatch, marker="POISON"):
    """Events whose summary carries ``marker`` fail in processing, every time."""
    from piia_engram.hooks import _processor

    original = _processor.process
    attempts: list[str] = []

    def process(event, root, engram=None):
        if marker in event["prepared"].get("summary", ""):
            attempts.append(event["event_id"])
            raise RuntimeError("this event cannot be processed")
        return original(event, root, engram)

    monkeypatch.setattr(_processor, "process", process)
    return attempts


def _queue_poison(root, count):
    return [spool.enqueue("cursor_writeback", "cursor", {"summary": f"POISON {SUMMARY} Case {n}."}, root=root)
            for n in range(count)]


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv(autodrain.ENV_SWITCH, raising=False)
    monkeypatch.delenv(autodrain.ENV_DELAY, raising=False)
    eng = Engram(root=tmp_path / "store")
    return eng


class TestBoundedDrain:
    def test_a_limit_stops_after_that_many_events_oldest_first(self, store):
        ids = _queue(store.root, 5)

        report = spool.drain(store.root, engram=store, max_events=2)

        assert report["processed"] == 2 and report["limited"] is True
        assert _pending(store.root) == 3
        left = sorted(p.name for p in spool.spool_dir(store.root).glob("*.jsonl"))
        assert [name.split("-", 1)[1][:-6] for name in left] == ids[2:]

    def test_without_a_limit_everything_is_processed(self, store):
        # Negative control: the command line drain is not limited.
        _queue(store.root, 5)
        report = spool.drain(store.root, engram=store)
        assert report["processed"] == 5 and report["limited"] is False
        assert _pending(store.root) == 0

    def test_a_deadline_already_passed_processes_nothing(self, store):
        _queue(store.root, 3)
        report = spool.drain(store.root, engram=store, deadline_seconds=0)
        assert report["processed"] == 0 and report["limited"] is True
        assert _pending(store.root) == 3

    def test_a_stop_request_ends_the_pass_between_events(self, store):
        _queue(store.root, 3)
        seen = []

        def should_stop():
            seen.append(1)
            return len(seen) > 1

        report = spool.drain(store.root, engram=store, should_stop=should_stop)
        assert report["processed"] == 1 and report["limited"] is True

    def test_each_event_is_processed_inside_the_guard(self, store, monkeypatch):
        from piia_engram.hooks import _processor

        _queue(store.root, 3)
        inside = [False]
        seen_inside: list[bool] = []
        original = _processor.process

        def process(event, root, engram=None):
            seen_inside.append(inside[0])
            return original(event, root, engram)

        monkeypatch.setattr(_processor, "process", process)

        class Guard:
            def __enter__(self):
                assert inside[0] is False  # one event at a time
                inside[0] = True

            def __exit__(self, *exc):
                inside[0] = False

        report = spool.drain(store.root, engram=store, event_guard=Guard)

        assert report["processed"] == 3
        assert seen_inside == [True, True, True]
        assert inside[0] is False  # released after the last event

    def test_without_a_guard_events_are_processed_outside_it(self, store, monkeypatch):
        # Negative control for the test above.
        from piia_engram.hooks import _processor

        _queue(store.root, 2)
        inside = [False]
        seen_inside: list[bool] = []
        original = _processor.process
        monkeypatch.setattr(_processor, "process", lambda event, root, engram=None: (
            seen_inside.append(inside[0]), original(event, root, engram))[1])

        spool.drain(store.root, engram=store)

        assert seen_inside == [False, False]

    def test_failing_events_do_not_use_up_the_limit(self, store, monkeypatch):
        _fail_marked_events(monkeypatch)
        _queue_poison(store.root, 3)
        good = _queue(store.root, 2)

        report = spool.drain(store.root, engram=store, max_events=2)

        assert report["failed"] == 3 and report["processed"] == 2
        receipts = {p.stem for p in (spool.spool_dir(store.root) / "receipts").glob("*.json")}
        assert receipts == set(good)

    def test_skipped_events_are_left_alone_and_counted(self, store):
        ids = _queue(store.root, 3)
        report = spool.drain(store.root, engram=store, skip_event=lambda name: ids[0] in name)
        assert report["skipped"] == 1 and report["processed"] == 2
        assert [p.name.split("-", 1)[1][:-6] for p in spool.spool_dir(store.root).glob("*.jsonl")] == [ids[0]]

    def test_the_caller_is_told_which_events_failed(self, store, monkeypatch):
        _fail_marked_events(monkeypatch)
        bad = _queue_poison(store.root, 2)
        _queue(store.root, 1)
        told: list[str] = []

        report = spool.drain(store.root, engram=store, on_event_failed=told.append)

        assert sorted(name.split("-", 1)[1][:-6] for name in told) == sorted(bad)
        assert "failed_events" not in report and all(b not in json.dumps(report) for b in bad)


class TestNeverAnAiWrite:
    def test_rows_written_inside_an_ai_call_are_local_and_pending(self, store):
        _queue(store.root, 2)

        with origin_scope(ORIGIN_MCP, client_name="agent"):
            report = spool.drain(store.root, engram=store)

        assert report["processed"] == 2
        rows = _rows(store.root, "lessons.json") + _rows(store.root, "decisions.json")
        assert rows, "the summaries should have produced review items"
        assert {row["provenance"]["origin"] for row in rows} == {"local"}
        assert {row["tier"] for row in rows} == {"staging"}

    @pytest.mark.parametrize("approval", ["strict", ""])
    def test_processing_approves_nothing(self, tmp_path, monkeypatch, approval):
        if approval:
            monkeypatch.setenv("ENGRAM_APPROVAL", approval)
        else:
            monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
        eng = Engram(root=tmp_path / "store")
        eng.add_lesson({"summary": "Approved before the queue ran.", "tier": "verified"})
        before = _trusted(eng.root)
        _queue(eng.root, 3)

        report = autodrain.run_pass(eng.root, source="test", engram=eng)

        assert report["processed"] == 3
        assert _trusted(eng.root) == before


class TestRunPass:
    def test_an_empty_queue_does_nothing_and_writes_nothing(self, store):
        before = {str(p): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
        assert autodrain.run_pass(store.root, source="test", engram=store) is None
        assert {str(p): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == before

    def test_the_switch_turns_it_off(self, store, monkeypatch):
        # Negative control: this is the behaviour before automatic processing.
        _queue(store.root, 2)
        monkeypatch.setenv(autodrain.ENV_SWITCH, "0")

        assert autodrain.enabled() is False
        assert autodrain.run_pass(store.root, source="test", engram=store) is None
        assert _pending(store.root) == 2

    @pytest.mark.parametrize("value, expected", [("", True), ("1", True), ("true", True), ("on", True),
                                                 ("0", False), ("false", False), ("off", False), ("no", False)])
    def test_the_switch_values(self, monkeypatch, value, expected):
        monkeypatch.setenv(autodrain.ENV_SWITCH, value)
        assert autodrain.enabled() is expected

    def test_a_pass_is_limited_and_oldest_first(self, store, monkeypatch):
        monkeypatch.setattr(autodrain, "PASS_MAX_EVENTS", 2)
        _queue(store.root, 5)

        report = autodrain.run_pass(store.root, source="test", engram=store)

        assert report["processed"] == 2 and report["limited"] is True
        assert _pending(store.root) == 3

    def test_the_last_pass_is_recorded_as_metadata_only(self, store):
        _queue(store.root, 2)
        autodrain.run_pass(store.root, source="mcp-server", engram=store)

        status = json.loads(autodrain.status_path(store.root).read_text(encoding="utf-8"))

        assert status["source"] == "mcp-server" and status["processed"] == 2
        assert set(status) == {"schema", "source", "ran_at", "processed", "duplicates", "failed",
                               "quarantined", "pending", "skipped", "busy", "limited", "stopped"}
        assert status["stopped"] == ""
        assert SUMMARY not in json.dumps(status)

    def test_a_busy_queue_is_skipped_quietly(self, store):
        _queue(store.root, 1)
        with spool._lock(spool.spool_dir(store.root)):
            report = autodrain.run_pass(store.root, source="test", engram=store)
        assert report["busy"] is True and report["processed"] == 0
        assert _pending(store.root) == 1
        log = store.root / "logs" / "hooks.log"
        assert not log.exists() or "drain failed" not in log.read_text(encoding="utf-8")

    def test_two_passes_at_once_process_each_event_once(self, store):
        ids = _queue(store.root, 6)
        other = Engram(root=store.root)
        reports = []
        threads = [threading.Thread(target=lambda e=e: reports.append(
            autodrain.run_pass(store.root, source="test", engram=e))) for e in (store, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not any(thread.is_alive() for thread in threads)
        autodrain.run_pass(store.root, source="test", engram=store)  # whatever a busy pass left

        receipts = sorted(p.stem for p in (spool.spool_dir(store.root) / "receipts").glob("*.json"))
        assert receipts == sorted(ids)
        assert _pending(store.root) == 0

    def test_a_failure_in_the_pass_is_swallowed(self, store, monkeypatch):
        _queue(store.root, 1)

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(spool, "drain", boom)
        assert autodrain.run_pass(store.root, source="test", engram=store) is None
        assert _pending(store.root) == 1


class TestNextWait:
    BASE = {"processed": 0, "failed": 0, "duplicates": 0, "quarantined": 0, "pending": 0,
            "busy": False, "limited": False}

    def test_nothing_to_do_waits_the_interval(self):
        assert autodrain.next_wait(None, 600.0) == autodrain.INTERVAL_SECONDS

    def test_leftover_events_come_soon(self):
        report = {**self.BASE, "processed": 50, "pending": 10, "limited": True}
        assert autodrain.next_wait(report, 600.0) == autodrain.CATCH_UP_SECONDS

    def test_a_pass_that_only_failed_backs_off_up_to_two_hours(self):
        report = {**self.BASE, "failed": 3, "pending": 3}
        assert autodrain.next_wait(report, 600.0) == 1200.0
        assert autodrain.next_wait(report, 1200.0) == 2400.0
        assert autodrain.next_wait(report, 7000.0) == autodrain.MAX_BACKOFF_SECONDS

    def test_progress_resets_the_backoff(self):
        report = {**self.BASE, "processed": 1, "failed": 1, "pending": 1}
        assert autodrain.next_wait(report, 2400.0) == autodrain.INTERVAL_SECONDS

    def test_a_missing_key_is_looked_at_again_after_two_hours(self):
        report = {**self.BASE, "failed": 1, "pending": 1, "key_unavailable": True}
        assert autodrain.next_wait(report, 600.0) == autodrain.MAX_BACKOFF_SECONDS

    def test_a_pass_that_only_stepped_over_failures_waits_the_interval(self):
        report = {**self.BASE, "skipped": 51, "pending": 51}
        assert autodrain.next_wait(report, 2400.0) == autodrain.INTERVAL_SECONDS


class TestFirstDelay:
    @pytest.mark.parametrize("value, expected", [("", 15.0), ("2", 2.0), ("0", 0.0), ("0.5", 0.5),
                                                 ("-1", 15.0), ("abc", 15.0), ("nan", 15.0), ("99999", 3600.0)])
    def test_values(self, monkeypatch, value, expected):
        monkeypatch.setenv(autodrain.ENV_DELAY, value)
        assert autodrain.first_delay() == expected


class TestBackgroundThread:
    @pytest.fixture
    def drainer(self):
        made: list[autodrain.AutoDrainer] = []

        def make(*args, **kwargs):
            item = autodrain.AutoDrainer(*args, **kwargs)
            made.append(item)
            return item

        yield make
        for item in made:
            item.stop()
            item.join(timeout=30)
            assert not item.is_alive(), "a queue thread outlived its test"

    def test_it_processes_the_queue_and_stops_on_request(self, store, drainer):
        _queue(store.root, 2)
        done = threading.Event()
        item = drainer(store.root, engram=store, delay=0, on_pass=lambda report: done.set())

        item.start()

        assert done.wait(timeout=60)
        item.stop()
        item.join(timeout=30)
        assert not item.is_alive()
        assert _pending(store.root) == 0

    def test_stop_during_the_first_delay_processes_nothing(self, store, drainer):
        _queue(store.root, 1)
        item = drainer(store.root, engram=store, delay=3600)
        item.start()
        item.stop()
        item.join(timeout=30)
        assert not item.is_alive()
        assert _pending(store.root) == 1

    def test_it_is_a_daemon_thread_with_a_name(self, store, drainer):
        item = drainer(store.root, engram=store, delay=3600)
        item.start()
        assert item.thread.daemon is True and item.thread.name == "engram-spool-drain"

    def test_a_missing_key_does_not_end_the_thread_or_spin_it(self, store, drainer, monkeypatch):
        calls = []
        first = threading.Event()

        def run_pass(root, **kwargs):
            calls.append(1)
            first.set()
            return {"processed": 0, "failed": 1, "pending": 1, "key_unavailable": True}

        monkeypatch.setattr(autodrain, "run_pass", run_pass)
        item = drainer(store.root, engram=store, delay=0)
        item.start()
        assert first.wait(timeout=30)
        time.sleep(0.3)
        assert item.is_alive() and calls == [1]  # waiting for the long retry, not looping
        item.stop()
        item.join(timeout=30)
        assert not item.is_alive()

    def test_events_wait_for_the_servers_write_lock(self, store, drainer):
        _queue(store.root, 1)
        lock = threading.RLock()
        done = threading.Event()
        item = drainer(store.root, engram=store, delay=0, event_guard=lambda: lock,
                       on_pass=lambda report: done.set())
        with lock:
            item.start()
            time.sleep(0.5)
            assert _pending(store.root) == 1  # a tool call holds the lock
        assert done.wait(timeout=60)
        assert _pending(store.root) == 0


class TestServerStart:
    @pytest.fixture
    def server(self, monkeypatch):
        from piia_engram import mcp_server

        started: list[autodrain.AutoDrainer] = []
        original = autodrain.AutoDrainer.start

        def start(self):
            started.append(self)
            return original(self)

        monkeypatch.setattr(autodrain.AutoDrainer, "start", start)
        monkeypatch.delenv(autodrain.ENV_SWITCH, raising=False)
        monkeypatch.setenv(autodrain.ENV_DELAY, "3600")
        yield mcp_server, started
        for item in started:
            item.stop()
            item.join(timeout=30)
            assert not item.is_alive()

    def test_a_writable_server_starts_the_queue_thread(self, server, store, monkeypatch):
        mcp_server, started = server
        monkeypatch.setattr(mcp_server, "_engram", store)

        item = mcp_server._start_spool_autodrain()

        assert started == [item] and item.is_alive()
        assert item.root == store.root

    def test_a_read_only_server_starts_none(self, server, store, monkeypatch):
        mcp_server, started = server
        monkeypatch.setattr(mcp_server, "_engram", Engram(root=store.root, read_only=True))
        assert mcp_server._start_spool_autodrain() is None and started == []

    def test_a_server_without_a_store_starts_none(self, server, monkeypatch):
        mcp_server, started = server
        monkeypatch.setattr(mcp_server, "_engram", None)
        assert mcp_server._start_spool_autodrain() is None and started == []

    def test_the_switch_starts_none(self, server, store, monkeypatch):
        mcp_server, started = server
        monkeypatch.setattr(mcp_server, "_engram", store)
        monkeypatch.setenv(autodrain.ENV_SWITCH, "0")
        assert mcp_server._start_spool_autodrain() is None and started == []

    def test_the_thread_takes_the_servers_write_lock_and_stops_at_shutdown(self, server, store, monkeypatch):
        mcp_server, started = server
        monkeypatch.setattr(mcp_server, "_engram", store)
        monkeypatch.setattr(mcp_server, "_shutting_down", False)
        item = mcp_server._start_spool_autodrain()
        with item.event_guard():
            assert mcp_server._write_operation_lock._is_owned()
        assert not mcp_server._write_operation_lock._is_owned()
        assert item.should_stop() is False
        monkeypatch.setattr(mcp_server, "_shutting_down", True)
        assert item.should_stop() is True


class TestWatcher:
    @pytest.fixture
    def watcher_env(self, store, monkeypatch):
        monkeypatch.setenv("ENGRAM_DIR", str(store.root))
        return store

    def test_a_scan_processes_the_queue(self, watcher_env):
        _queue(watcher_env.root, 2)
        report = autodrain.watcher_pass()
        assert report["processed"] == 2 and _pending(watcher_env.root) == 0

    def test_at_most_one_pass_a_minute(self, watcher_env):
        clock = [1000.0]
        _queue(watcher_env.root, 1)
        assert autodrain.watcher_pass(now=lambda: clock[0])["processed"] == 1
        _queue(watcher_env.root, 1)
        clock[0] += 30
        assert autodrain.watcher_pass(now=lambda: clock[0]) is None
        assert _pending(watcher_env.root) == 1
        clock[0] += 31
        assert autodrain.watcher_pass(now=lambda: clock[0])["processed"] == 1

    def test_scan_once_calls_it(self, watcher_env, monkeypatch):
        from piia_engram.watcher import core

        calls = []
        monkeypatch.setattr(autodrain, "watcher_pass", lambda: calls.append(1))
        monkeypatch.setattr(core, "_load_state", lambda: {})
        monkeypatch.setattr(core, "_save_state", lambda *args, **kwargs: None)

        core.scan_once([])

        assert calls == [1]

    def test_the_switch_leaves_the_queue_alone(self, watcher_env, monkeypatch):
        # Negative control.
        monkeypatch.setenv(autodrain.ENV_SWITCH, "0")
        _queue(watcher_env.root, 1)
        assert autodrain.watcher_pass() is None
        assert _pending(watcher_env.root) == 1


class TestDoctor:
    def test_backlog_reports_the_switch_and_the_last_pass(self, store, monkeypatch):
        _queue(store.root, 1)
        assert spool.backlog(store.root)["auto_drain"] == {"enabled": True, "last": None}

        autodrain.run_pass(store.root, source="watcher", engram=store)
        last = spool.backlog(store.root)["auto_drain"]["last"]
        assert last["source"] == "watcher" and last["processed"] == 1

        monkeypatch.setenv(autodrain.ENV_SWITCH, "0")
        assert spool.backlog(store.root)["auto_drain"]["enabled"] is False

    def test_a_damaged_status_file_reports_no_last_pass(self, store):
        path = autodrain.status_path(store.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert spool.backlog(store.root)["auto_drain"]["last"] is None

    def test_doctor_prints_both_lines(self, store, capsys, monkeypatch):
        from piia_engram import doctor

        _queue(store.root, 1)
        autodrain.run_pass(store.root, source="mcp-server", engram=store)
        capsys.readouterr()
        monkeypatch.setattr("piia_engram.connection_report.build_report",
                            lambda root, days: (_ for _ in ()).throw(RuntimeError("skip")))

        doctor._print_connection_report(store.root)
        out = capsys.readouterr().out

        assert "Automatic processing: not switched off in this terminal" in out
        assert "their own environment" in out
        assert "Last automatic pass:" in out and "mcp-server" in out and "1 processed" in out

    def test_doctor_says_when_it_is_off(self, store, capsys, monkeypatch):
        from piia_engram import doctor

        monkeypatch.setenv(autodrain.ENV_SWITCH, "0")
        monkeypatch.setattr("piia_engram.connection_report.build_report",
                            lambda root, days: (_ for _ in ()).throw(RuntimeError("skip")))
        doctor._print_connection_report(store.root)
        out = capsys.readouterr().out
        assert "Automatic processing: switched off in this terminal" in out
        assert "Last automatic pass: none recorded" in out


class TestServerMain:
    """``main()`` starts the queue thread next to the running server and ends it with it."""

    @pytest.fixture
    def run_main(self, store, monkeypatch):
        from types import SimpleNamespace

        from piia_engram import mcp_server

        started: list[autodrain.AutoDrainer] = []
        original = autodrain.AutoDrainer.start

        def start(self):
            started.append(self)
            return original(self)

        monkeypatch.setattr(autodrain.AutoDrainer, "start", start)
        monkeypatch.setenv(autodrain.ENV_DELAY, "3600")
        monkeypatch.setattr(mcp_server, "_engram", store)
        monkeypatch.setattr(mcp_server, "_parse_args", lambda: SimpleNamespace(
            transport="stdio", host="127.0.0.1", port=8123))
        monkeypatch.setattr(mcp_server, "_configure_utf8_stdio", lambda: None)
        monkeypatch.setattr(mcp_server, "_run_startup_auto_migrate", lambda: None)
        monkeypatch.setattr(mcp_server, "_start_claude_hook_check", lambda: None)
        alive_while_running: list[bool] = []
        monkeypatch.setattr(mcp_server.mcp, "run", lambda transport: alive_while_running.extend(
            item.is_alive() for item in started))

        def run():
            mcp_server.main()
            for item in started:
                item.join(timeout=30)
            return started, alive_while_running

        yield run
        for item in started:
            item.stop()
            item.join(timeout=30)
            assert not item.is_alive()

    def test_the_thread_runs_with_the_server_and_ends_with_it(self, run_main, monkeypatch):
        monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
        started, alive_while_running = run_main()
        assert len(started) == 1 and alive_while_running == [True]
        assert not started[0].is_alive()

    def test_an_ephemeral_start_has_no_queue_thread(self, run_main, monkeypatch):
        monkeypatch.setenv("ENGRAM_EPHEMERAL", "1")
        started, _ = run_main()
        assert started == []

    def test_the_switch_means_no_queue_thread(self, run_main, monkeypatch):
        # Negative control.
        monkeypatch.delenv("ENGRAM_EPHEMERAL", raising=False)
        monkeypatch.setenv(autodrain.ENV_SWITCH, "0")
        started, _ = run_main()
        assert started == []


class TestProtectedQueueWithoutItsKey:
    """A process that cannot open a protected queue leaves it to one that can."""

    @pytest.fixture
    def protected(self, tmp_path, monkeypatch):
        from piia_engram.hooks import _spool_protection

        monkeypatch.setattr(_spool_protection, "CRYPTO_BUDGET_SECONDS", 30)
        root = tmp_path / "protected"
        root.mkdir()
        (root / ".corpus_salt").write_bytes(b"0123456789abcdef")
        monkeypatch.setenv("ENGRAM_SECRET", "synthetic-queue-key")
        monkeypatch.setenv("ENGRAM_DIR", str(root))
        assert spool.enqueue("cursor_writeback", "cursor", {"summary": SUMMARY}, root=root,
                             deadline=time.monotonic() + 35)
        return root

    def test_a_missing_secret_is_reported_as_unavailable_here(self, protected, monkeypatch):
        monkeypatch.delenv("ENGRAM_SECRET")

        report = spool.drain(protected)

        assert report["processed"] == 0 and report["failed"] == 1
        assert report["key_unavailable"] is True
        assert _pending(protected) == 1  # untouched, for a process that has the key
        assert autodrain.next_wait(report, 600.0) == autodrain.MAX_BACKOFF_SECONDS

    def test_the_watcher_waits_two_hours_and_then_looks_again(self, protected, monkeypatch):
        monkeypatch.delenv("ENGRAM_SECRET")
        clock = [0.0]

        first = autodrain.watcher_pass(now=lambda: clock[0])
        clock[0] += 3600
        second = autodrain.watcher_pass(now=lambda: clock[0])
        clock[0] += 3601
        monkeypatch.setenv("ENGRAM_SECRET", "synthetic-queue-key")  # the owner supplied the key
        third = autodrain.watcher_pass(now=lambda: clock[0])

        assert first["key_unavailable"] is True and second is None
        assert third["processed"] == 1 and _pending(protected) == 0

    def test_a_missing_key_is_not_remembered_as_a_failed_event(self, protected, monkeypatch):
        monkeypatch.delenv("ENGRAM_SECRET")
        autodrain.run_pass(protected, source="test")
        assert autodrain._failed_at == {}

    def test_doctor_says_that_the_last_pass_stopped_and_why(self, protected, monkeypatch, capsys):
        from piia_engram import doctor

        monkeypatch.delenv("ENGRAM_SECRET")
        autodrain.run_pass(protected, source="mcp-server")
        assert autodrain.read_status(protected)["stopped"] == "key-unavailable"
        monkeypatch.setattr("piia_engram.connection_report.build_report",
                            lambda root, days: (_ for _ in ()).throw(RuntimeError("skip")))
        capsys.readouterr()

        doctor._print_connection_report(protected)
        out = capsys.readouterr().out

        assert "by mcp-server: stopped, the queue key is not available to that process" in out
        assert "ENGRAM_SECRET" in out and "1 left" in out

    def test_with_the_secret_the_same_queue_is_processed(self, protected):
        # Negative control.
        report = spool.drain(protected)
        assert report["processed"] == 1 and not report.get("key_unavailable")
        assert _pending(protected) == 0

    def test_a_passing_protection_failure_is_retried_soon(self, store, monkeypatch):
        from piia_engram.hooks._spool_protection import ProtectionError

        ids = _queue(store.root, 1)
        original = spool._read_event
        busy = [True]

        def read_event(path, codec=None):
            if busy[0]:
                raise ProtectionError("protection-unavailable")  # e.g. the policy lock was busy
            return original(path, codec)

        monkeypatch.setattr(spool, "_read_event", read_event)

        report = autodrain.run_pass(store.root, source="test", engram=store)

        assert report["failed"] == 1 and not report.get("key_unavailable")
        assert autodrain.next_wait(report, autodrain.INTERVAL_SECONDS) == 1200.0  # ordinary backoff
        assert autodrain.read_status(store.root)["stopped"] == ""
        assert autodrain._failed_at == {}  # not set aside: nothing is wrong with the event

        busy[0] = False
        report = autodrain.run_pass(store.root, source="test", engram=store)
        assert report["processed"] == 1 and _pending(store.root) == 0
        assert [p.stem for p in (spool.spool_dir(store.root) / "receipts").glob("*.json")] == ids


class TestFailingEventsAtTheHead:
    """Events that keep failing must not keep the ones behind them waiting."""

    def test_a_healthy_event_behind_51_failing_ones_is_processed(self, store, monkeypatch):
        _fail_marked_events(monkeypatch)
        _queue_poison(store.root, 51)
        (good,) = _queue(store.root, 1)
        assert autodrain.PASS_MAX_EVENTS == 50

        reports = [autodrain.run_pass(store.root, source="test", engram=store) for _ in range(3)]

        receipts = [p.stem for p in (spool.spool_dir(store.root) / "receipts").glob("*.json")]
        assert receipts == [good]
        assert _pending(store.root) == 51  # the failing ones stay, for a retry or the owner

    def test_failed_events_are_stepped_over_on_the_next_pass(self, store, monkeypatch):
        attempts = _fail_marked_events(monkeypatch)
        _queue_poison(store.root, 3)

        first = autodrain.run_pass(store.root, source="test", engram=store)
        second = autodrain.run_pass(store.root, source="test", engram=store)

        assert first["failed"] == 3 and first["skipped"] == 0
        assert second["failed"] == 0 and second["skipped"] == 3
        assert len(attempts) == 3
        assert autodrain.read_status(store.root)["skipped"] == 3

    def test_a_pass_that_runs_out_of_time_on_failures_still_gets_past_them(self, store, monkeypatch):
        # Each pass may look at five events only (a stand-in for the time limit).
        _fail_marked_events(monkeypatch)
        _queue_poison(store.root, 12)
        (good,) = _queue(store.root, 1)

        def five_events():
            seen = [0]

            def should_stop():
                seen[0] += 1
                return seen[0] > 5

            return should_stop

        for _ in range(4):
            autodrain.run_pass(store.root, source="test", engram=store, should_stop=five_events())

        assert [p.stem for p in (spool.spool_dir(store.root) / "receipts").glob("*.json")] == [good]

    def test_a_failed_event_is_tried_again_later(self, store, monkeypatch):
        attempts = _fail_marked_events(monkeypatch)
        _queue_poison(store.root, 1)
        autodrain.run_pass(store.root, source="test", engram=store)
        monkeypatch.setattr(autodrain, "RETRY_FAILED_AFTER_SECONDS", 0.0)

        report = autodrain.run_pass(store.root, source="test", engram=store)

        assert report["failed"] == 1 and report["skipped"] == 0 and len(attempts) == 2

    def test_the_drain_command_tries_every_event_every_time(self, store, monkeypatch):
        # Negative control: only the automatic passes step over failures.
        attempts = _fail_marked_events(monkeypatch)
        _queue_poison(store.root, 2)
        autodrain.run_pass(store.root, source="test", engram=store)

        report = spool.drain(store.root, engram=store)

        assert report["failed"] == 2 and report["skipped"] == 0 and len(attempts) == 4

    def test_a_busy_store_is_not_held_against_the_event(self, store, monkeypatch):
        import portalocker

        from piia_engram.hooks import _processor

        _queue(store.root, 1)

        def locked(event, root, engram=None):
            raise portalocker.LockException("store busy")

        monkeypatch.setattr(_processor, "process", locked)
        autodrain.run_pass(store.root, source="test", engram=store)
        assert autodrain._failed_at == {}

    def test_the_memory_of_failures_is_bounded(self, monkeypatch):
        monkeypatch.setattr(autodrain, "_MAX_REMEMBERED_FAILURES", 8)
        for n in range(40):
            autodrain._remember_failure(f"event-{n}")
        assert len(autodrain._failed_at) <= 8 and autodrain._recently_failed("event-39")


class TestProjectSnapshotTolerance:
    """A project file that is not UTF-8 must not fail every Stop event."""

    def _project(self, tmp_path):
        project = tmp_path / "project"
        (project / "tests").mkdir(parents=True)
        (project / "src").mkdir()
        (project / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
        (project / "tests" / "test_ok.py").write_text("def test_one():\n    pass\n", encoding="utf-8")
        (project / "tests" / "test_latin1.py").write_bytes(
            b"# caf\xe9 \xff\xfe\ndef test_two():\n    pass\n")
        return project

    def test_a_non_utf8_test_file_is_counted_not_fatal(self, store, tmp_path):
        from piia_engram.hooks import _processor

        project = self._project(tmp_path)

        _processor._snapshot(store, str(project))

        snapshot = store.get_project_snapshot(str(project))
        assert snapshot["version"] == "1.2.3" and snapshot["test_count"] == 2

    def test_a_non_utf8_manifest_is_not_fatal(self, store, tmp_path):
        from piia_engram.hooks import _processor

        project = self._project(tmp_path)
        (project / "pyproject.toml").write_bytes(b'# caf\xe9\n[project]\nversion = "2.0.0"\n')

        _processor._snapshot(store, str(project))

        assert store.get_project_snapshot(str(project))["version"] == "2.0.0"


class TestToolCallsGoFirst:
    """A tool call waits for the event being processed, not for the whole pass."""

    def test_a_waiting_tool_call_gets_the_lock_after_one_event(self, store, monkeypatch):
        from piia_engram import mcp_server
        from piia_engram.hooks import _processor

        _queue(store.root, 6)
        original = _processor.process
        processed: list[str] = []
        first_started = threading.Event()

        def slow(event, root, engram=None):
            first_started.set()
            time.sleep(0.2)
            result = original(event, root, engram)
            processed.append(event["event_id"])
            return result

        monkeypatch.setattr(_processor, "process", slow)
        seen_by_tool: list[int] = []

        def tool_call():
            first_started.wait(timeout=30)
            with mcp_server._tool_write_lock():
                seen_by_tool.append(len(processed))

        tool = threading.Thread(target=tool_call, name="tool-call")
        tool.start()
        try:
            report = spool.drain(store.root, engram=store, event_guard=mcp_server._queue_event_lock)
        finally:
            tool.join(timeout=60)
        assert not tool.is_alive()

        assert report["processed"] == 6
        assert seen_by_tool and seen_by_tool[0] <= 1, seen_by_tool  # after the event in progress
        assert mcp_server._tool_waiters == 0
        assert not mcp_server._write_operation_lock._is_owned()

    def test_the_tool_lock_is_reentrant_and_balanced(self):
        from piia_engram import mcp_server

        with mcp_server._tool_write_lock():
            with mcp_server._tool_write_lock():
                assert mcp_server._write_operation_lock._is_owned()
        assert not mcp_server._write_operation_lock._is_owned()
        assert mcp_server._tool_waiters == 0

    def test_the_queue_thread_does_not_wait_forever_for_tool_calls(self, monkeypatch):
        from piia_engram import mcp_server

        monkeypatch.setattr(mcp_server, "_tool_waiters", 1)  # a call that never arrives
        monkeypatch.setattr(mcp_server, "_QUEUE_YIELD_SECONDS", 0.2)
        started = time.monotonic()
        with mcp_server._queue_event_lock():
            waited = time.monotonic() - started
        assert 0.15 <= waited < 5
