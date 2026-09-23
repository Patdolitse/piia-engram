"""hold_directory_lock: one lock acquisition across several writes in a directory."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from piia_engram import storage


def test_writes_inside_a_held_lock_do_not_wait_for_it(tmp_path: Path):
    target = tmp_path / "state.json"
    started = time.monotonic()
    with storage.hold_directory_lock(tmp_path):
        storage._update_json(target, lambda cur: {**cur, "a": 1}, default={})
        storage._atomic_write_json(tmp_path / "other.json", {"b": 2})
        storage._append_jsonl_lines(tmp_path / "log.jsonl", ['{"c": 3}'])
    assert time.monotonic() - started < 2
    assert storage._read_json(target) == {"a": 1}
    assert storage._read_json(tmp_path / "other.json") == {"b": 2}


def test_another_thread_waits_until_the_lock_is_released(tmp_path: Path):
    target = tmp_path / "state.json"
    order: list[str] = []

    def _writer():
        storage._update_json(target, lambda cur: {**cur, "thread": True}, default={})
        order.append("thread wrote")

    with storage.hold_directory_lock(tmp_path):
        worker = threading.Thread(target=_writer)
        worker.start()
        time.sleep(0.5)
        order.append("holder done")
    worker.join(timeout=10)
    assert order == ["holder done", "thread wrote"]


def test_the_held_set_is_cleared_after_an_error(tmp_path: Path):
    with pytest.raises(ValueError):
        with storage.hold_directory_lock(tmp_path):
            raise ValueError("boom")
    assert storage._HELD_DIRECTORY_LOCKS.get() == frozenset()


def test_holding_twice_in_the_same_context_is_allowed(tmp_path: Path):
    with storage.hold_directory_lock(tmp_path):
        with storage.hold_directory_lock(tmp_path):
            storage._atomic_write_json(tmp_path / "x.json", {"ok": True})
    assert storage._read_json(tmp_path / "x.json") == {"ok": True}
