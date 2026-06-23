"""C1-3: playbook access-count bump must use locked read-modify-write.

Bug: get_playbooks/get_playbook increment access_count via unlocked
_write_playbook_file. Under concurrency, a concurrent delete can be
overwritten (resurrecting the playbook), or counts can be lost.
"""

from __future__ import annotations

import threading

import pytest

from piia_engram.core import Engram


class TestAccessCountConcurrency:
    @pytest.fixture()
    def eng_with_playbook(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Test playbook",
            "description": "For concurrency test",
            "steps": [{"action": "Step 1", "detail": "do it"}],
        })
        pbs = eng.get_playbooks(_update_access=False)
        assert len(pbs) >= 1
        return eng, pbs[0]["id"]

    def test_concurrent_access_count_increments(self, eng_with_playbook):
        """Multiple threads bumping access_count must not lose increments."""
        eng, pb_id = eng_with_playbook
        n_threads = 6
        barrier = threading.Barrier(n_threads, timeout=5)
        errors: list[Exception] = []

        def bump():
            try:
                barrier.wait()
                eng.get_playbook(pb_id, _update_access=True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=bump) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"
        final = eng._read_playbook_by_id(pb_id)
        assert final is not None
        assert final["access_count"] >= n_threads, (
            f"Expected access_count >= {n_threads}, got {final['access_count']}"
        )

    def test_delete_not_resurrected_by_access_bump(self, eng_with_playbook):
        """A delete concurrent with an access bump must not resurrect the playbook."""
        eng, pb_id = eng_with_playbook
        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def access_bump():
            try:
                barrier.wait()
                eng.get_playbook(pb_id, _update_access=True)
            except Exception as e:
                errors.append(e)

        def delete_pb():
            try:
                barrier.wait()
                eng.delete_playbook(pb_id, dry_run=False, confirm=True)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=access_bump)
        t2 = threading.Thread(target=delete_pb)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        final = eng._read_playbook_by_id(pb_id)
        if final is not None:
            assert final.get("status") != "active", (
                "Deleted playbook was resurrected by concurrent access bump"
            )

    def test_get_playbook_does_not_bump_deleted(self, tmp_path):
        """get_playbook must not bump access_count / last_reviewed on deleted playbooks."""
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Deletable playbook for status check",
            "description": "Will be deleted then accessed",
            "steps": [{"action": "Step 1", "detail": "do it"}],
        })
        pbs = eng.get_playbooks(_update_access=False)
        assert len(pbs) >= 1
        pb_id = pbs[0]["id"]

        before = eng._read_playbook_by_id(pb_id)
        before_count = before["access_count"]
        before_reviewed = before["last_reviewed"]

        eng.delete_playbook(pb_id, dry_run=False, confirm=True)

        eng.get_playbook(pb_id, _update_access=True)

        after = eng._read_playbook_by_id(pb_id)
        assert after is not None
        assert after["access_count"] == before_count, (
            f"Deleted playbook access_count changed: {before_count} → {after['access_count']}"
        )
        assert after["last_reviewed"] == before_reviewed, (
            "Deleted playbook last_reviewed was modified"
        )

    def test_get_playbook_does_not_bump_outdated(self, tmp_path):
        """get_playbook must not bump access_count on outdated playbooks."""
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Outdatable playbook for status check",
            "description": "Will be outdated then accessed",
            "steps": [{"action": "Step 1", "detail": "do it"}],
        })
        pbs = eng.get_playbooks(_update_access=False)
        assert len(pbs) >= 1
        pb_id = pbs[0]["id"]

        eng._update_playbook_file_by_id(
            pb_id, lambda p: {**p, "status": "outdated"}
        )

        before = eng._read_playbook_by_id(pb_id)
        before_count = before["access_count"]

        eng.get_playbook(pb_id, _update_access=True)

        after = eng._read_playbook_by_id(pb_id)
        assert after["access_count"] == before_count, (
            f"Outdated playbook access_count changed: {before_count} → {after['access_count']}"
        )
