"""C2-2: watcher_state.json must use file locking.

Bug: _load_state / _save_state use raw Path.read_text / write_text
with no lock. Multiple watchers overwrite each other's watermarks.
"""

from __future__ import annotations

import json
import threading

import pytest

from piia_engram.watcher import core as watcher_core


class TestWatcherStateLock:
    @pytest.fixture(autouse=True)
    def _patch_state_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watcher_core, "_state_dir", lambda: tmp_path)

    def test_concurrent_saves_no_lost_keys(self):
        """Two threads saving different keys must both persist."""
        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def save_a():
            try:
                barrier.wait()
                state = watcher_core._load_state()
                state["key_a"] = "value_a"
                watcher_core._save_state(state)
            except Exception as e:
                errors.append(e)

        def save_b():
            try:
                barrier.wait()
                state = watcher_core._load_state()
                state["key_b"] = "value_b"
                watcher_core._save_state(state)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=save_a)
        t2 = threading.Thread(target=save_b)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        final = watcher_core._load_state()
        assert "key_a" in final, "key_a lost in concurrent save"
        assert "key_b" in final, "key_b lost in concurrent save"

    def test_rapid_sequential_saves_preserve_all(self):
        """10 sequential save-load cycles must preserve all keys."""
        for i in range(10):
            state = watcher_core._load_state()
            state[f"seq_{i}"] = i
            watcher_core._save_state(state)

        final = watcher_core._load_state()
        for i in range(10):
            assert f"seq_{i}" in final, f"seq_{i} lost after sequential saves"
