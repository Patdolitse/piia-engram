"""M3 should-fix: save_agent_context concurrent append must not lose entries.

Code review C2-1: save_agent_context uses read-all → concat → write-all
without any lock. Two concurrent appends read the same state; the slower
writer overwrites the faster one's entry. Measured 71% loss rate in stress.

Fix: acquire per-directory portalocker lock, then append inside the lock.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from piia_engram.core import Engram


THREAD_COUNT = 8
WRITES_PER_THREAD = 5


class TestConcurrentAppendNoLoss:
    def test_all_entries_survive_concurrent_append(self, tmp_path):
        """Spawn THREAD_COUNT threads each appending WRITES_PER_THREAD entries
        to the same session. All entries must appear in the final file."""
        eng = Engram(root=tmp_path)
        session_id = "concurrent-test"
        errors: list[Exception] = []

        def worker(thread_idx: int):
            try:
                for write_idx in range(WRITES_PER_THREAD):
                    eng.save_agent_context(
                        tool="test_tool",
                        content=f"ENTRY-{thread_idx}-{write_idx}",
                        session_id=session_id,
                    )
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREAD_COUNT)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"Worker errors: {errors}"

        file_path = tmp_path / "contexts" / "test_tool" / f"{session_id}.md"
        assert file_path.exists(), "Session file not created"
        content = file_path.read_text(encoding="utf-8")

        expected_total = THREAD_COUNT * WRITES_PER_THREAD
        found = sum(
            1 for i in range(THREAD_COUNT) for j in range(WRITES_PER_THREAD)
            if f"ENTRY-{i}-{j}" in content
        )
        assert found == expected_total, (
            f"Lost {expected_total - found}/{expected_total} entries "
            f"({100 * (expected_total - found) / expected_total:.0f}% loss)"
        )

    def test_concurrent_create_no_header_loss(self, tmp_path):
        """Two threads creating the same session_id simultaneously must not
        lose the header or either entry."""
        eng = Engram(root=tmp_path)
        barrier = threading.Barrier(2, timeout=5)
        results: list[dict] = [None, None]  # type: ignore[list-item]

        def worker(idx: int):
            barrier.wait()
            results[idx] = eng.save_agent_context(
                tool="test_tool",
                content=f"FIRST-WRITE-{idx}",
                session_id="race-create",
            )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        file_path = tmp_path / "contexts" / "test_tool" / "race-create.md"
        content = file_path.read_text(encoding="utf-8")
        assert "FIRST-WRITE-0" in content
        assert "FIRST-WRITE-1" in content
