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


class TestDeepMergeAndMonotonicWatermark:
    """1-4: _save_state must deep-merge tool sub-dicts and never regress offsets."""

    @pytest.fixture(autouse=True)
    def _patch_state_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(watcher_core, "_state_dir", lambda: tmp_path)

    def test_deep_merge_preserves_sibling_file_keys(self):
        """Two saves updating different per-file entries under the same tool
        must preserve both file entries (deep merge, not shallow replace)."""
        # Save 1: tool "codex" has file_a
        watcher_core._save_state({
            "codex": {
                "file_a": {"mtime": 100, "size": 500, "offset": 500},
            }
        })
        # Save 2: tool "codex" has file_b (simulates a concurrent scan
        # that only discovered file_b)
        watcher_core._save_state({
            "codex": {
                "file_b": {"mtime": 200, "size": 800, "offset": 800},
            }
        })

        final = watcher_core._load_state()
        codex = final.get("codex", {})
        assert "file_a" in codex, (
            "file_a lost: shallow merge replaced entire codex dict"
        )
        assert "file_b" in codex, "file_b not saved"

    def test_monotonic_watermark_offset_never_regresses(self):
        """A stale save with a smaller offset must not overwrite a newer one."""
        # Initial state: file_x at offset 1000
        watcher_core._save_state({
            "codex": {
                "file_x": {"mtime": 100, "size": 1200, "offset": 1000},
            }
        })
        # Stale save tries to set offset back to 500
        watcher_core._save_state({
            "codex": {
                "file_x": {"mtime": 100, "size": 1200, "offset": 500},
            }
        })

        final = watcher_core._load_state()
        entry = final["codex"]["file_x"]
        assert entry["offset"] >= 1000, (
            f"Watermark regressed: offset={entry['offset']}, expected >= 1000"
        )

    def test_concurrent_deep_merge_no_lost_file_keys(self):
        """Two threads saving different file keys under the same tool
        must both survive (deep merge under lock)."""
        # Seed state with tool key
        watcher_core._save_state({"codex": {}})
        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def save_file_a():
            try:
                barrier.wait()
                watcher_core._save_state({
                    "codex": {
                        "file_a": {"mtime": 1, "size": 100, "offset": 100},
                    }
                })
            except Exception as e:
                errors.append(e)

        def save_file_b():
            try:
                barrier.wait()
                watcher_core._save_state({
                    "codex": {
                        "file_b": {"mtime": 2, "size": 200, "offset": 200},
                    }
                })
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=save_file_a)
        t2 = threading.Thread(target=save_file_b)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        final = watcher_core._load_state()
        codex = final.get("codex", {})
        assert "file_a" in codex, "file_a lost in concurrent deep merge"
        assert "file_b" in codex, "file_b lost in concurrent deep merge"

    def test_mtime_advances_forward_only(self):
        """A stale save with an older mtime must not overwrite a newer one."""
        watcher_core._save_state({
            "codex": {
                "file_y": {"mtime": 999.0, "size": 500, "offset": 500},
            }
        })
        watcher_core._save_state({
            "codex": {
                "file_y": {"mtime": 100.0, "size": 500, "offset": 500},
            }
        })
        final = watcher_core._load_state()
        entry = final["codex"]["file_y"]
        assert entry["mtime"] >= 999.0, (
            f"mtime regressed: {entry['mtime']}, expected >= 999.0"
        )
