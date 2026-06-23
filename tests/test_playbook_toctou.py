"""M4 should-fix: update_playbook concurrent calls must not lose updates.

Code review C1-1/C1-2: update_playbook reads the playbook OUTSIDE any lock
via _read_playbook_by_id, modifies in memory, then writes back via
_write_playbook_and_index. Two concurrent update_playbook calls for the same
playbook read the same version — the slower writer overwrites the faster one's
changes.

Fix: use the existing _update_playbook_file_by_id (locked read-modify-write)
instead of the unlocked read → modify → write pattern.
"""

from __future__ import annotations

import threading

import pytest

from piia_engram.core import Engram


class TestUpdatePlaybookConcurrency:
    @pytest.fixture()
    def eng_with_playbook(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Deploy checklist",
            "description": "Steps to deploy",
            "steps": [{"action": "Build", "detail": "run build"}],
        })
        pbs = eng.get_playbooks()
        assert len(pbs) >= 1
        return eng, pbs[0]["id"]

    def test_concurrent_updates_no_lost_writes(self, eng_with_playbook):
        """Two threads updating different fields of the same playbook must
        both see their changes reflected in the final state."""
        eng, pb_id = eng_with_playbook
        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def update_description():
            try:
                barrier.wait()
                eng.update_playbook(pb_id, {"description": "UPDATED-BY-THREAD-A"})
            except Exception as e:
                errors.append(e)

        def update_title():
            try:
                barrier.wait()
                eng.update_playbook(pb_id, {"title": "UPDATED-BY-THREAD-B"})
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=update_description)
        t2 = threading.Thread(target=update_title)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        final = eng._read_playbook_by_id(pb_id)
        assert final is not None

        # Both updates must survive. Without proper locking, one will be lost.
        # We can't predict which thread runs second, but the second writer
        # must have seen the first writer's change.
        has_a = final.get("description") == "UPDATED-BY-THREAD-A"
        has_b = final.get("title") == "UPDATED-BY-THREAD-B"
        assert has_a and has_b, (
            f"Lost update: description={final.get('description')!r}, "
            f"title={final.get('title')!r}. "
            "Expected both UPDATED-BY-THREAD-A and UPDATED-BY-THREAD-B."
        )

    def test_version_increments_correctly(self, eng_with_playbook):
        """Sequential updates must each see the incremented version."""
        eng, pb_id = eng_with_playbook
        v1 = eng._read_playbook_by_id(pb_id)
        initial_version = v1.get("version", 1)

        eng.update_playbook(pb_id, {"description": "v2"})
        eng.update_playbook(pb_id, {"description": "v3"})

        final = eng._read_playbook_by_id(pb_id)
        assert final["version"] == initial_version + 2, (
            f"Expected version {initial_version + 2}, got {final['version']}"
        )


class TestPlaybookBodyIndexAtomicity:
    """Body + index writes must be transactional: either both succeed or
    neither is modified. (1-1 crash-safety fix)"""

    def test_failed_index_update_rolls_back_body(self, tmp_path):
        """If the index update fails, the body file must not be orphaned."""
        from unittest.mock import patch

        eng = Engram(root=tmp_path)
        pb_dir = tmp_path / "playbooks"

        body_files_before = set(pb_dir.glob("*.json")) if pb_dir.exists() else set()
        idx_before = set()
        if (pb_dir / "_index.json").exists():
            import json
            idx_before = {
                e.get("id") for e in
                json.loads((pb_dir / "_index.json").read_text(encoding="utf-8"))
            }

        original_update = eng._update_playbook_index

        def _boom(mutator):
            raise OSError("simulated index write failure")

        with patch.object(eng, "_update_playbook_index", side_effect=_boom):
            with pytest.raises(OSError, match="simulated"):
                eng.add_playbook({
                    "title": "Orphan candidate playbook",
                    "trigger": "should not persist",
                    "steps": [{"action": "noop"}],
                })

        body_files_after = set(pb_dir.glob("*.json")) if pb_dir.exists() else set()
        new_bodies = body_files_after - body_files_before - {pb_dir / "_index.json"}
        assert not new_bodies, (
            f"Orphaned body file(s) without index entry: {[f.name for f in new_bodies]}"
        )

        if (pb_dir / "_index.json").exists():
            import json
            idx_after = {
                e.get("id") for e in
                json.loads((pb_dir / "_index.json").read_text(encoding="utf-8"))
            }
            assert idx_after == idx_before, "Index was modified despite failure"

    def test_successful_add_writes_both_body_and_index(self, tmp_path):
        """Normal add must create both body file and index entry."""
        import json

        eng = Engram(root=tmp_path)
        result = eng.add_playbook({
            "title": "Valid playbook for atomicity check",
            "trigger": "manual",
            "steps": [{"action": "deploy"}],
        })
        pb_id = result.get("id")
        assert pb_id

        body_path = tmp_path / "playbooks" / f"{pb_id}.json"
        assert body_path.exists(), "Body file not created"

        idx_path = tmp_path / "playbooks" / "_index.json"
        assert idx_path.exists(), "Index not created"
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
        idx_ids = {e.get("id") for e in idx}
        assert pb_id in idx_ids, f"Playbook {pb_id} not in index"
