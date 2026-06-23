"""C1-4: execution plan read-modify-write must be locked.

Bug: update_execution_step and save_execution_plan use unlocked
_read_json → mutate → _write_json, losing step state under concurrency.
"""

from __future__ import annotations

import threading

import pytest

from piia_engram.core import Engram


class TestExecutionPlanConcurrency:
    @pytest.fixture()
    def eng_with_plan(self, tmp_path):
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Deploy checklist",
            "description": "3-step deploy",
            "steps": [
                {"action": "Build", "detail": "run build"},
                {"action": "Test", "detail": "run tests"},
                {"action": "Ship", "detail": "deploy"},
            ],
        })
        pbs = eng.get_playbooks(_update_access=False)
        pb_id = pbs[0]["id"]
        plan = eng.prepare_playbook_execution(pb_id)
        eng.save_execution_plan(plan)
        return eng, pb_id

    def test_concurrent_step_updates_no_lost_writes(self, eng_with_plan):
        """Two threads updating different steps must both persist."""
        eng, pb_id = eng_with_plan
        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def update_step_1():
            try:
                barrier.wait()
                eng.update_execution_step(pb_id, 1, "completed", notes="done-1")
            except Exception as e:
                errors.append(e)

        def update_step_2():
            try:
                barrier.wait()
                eng.update_execution_step(pb_id, 2, "skipped", notes="skip-2")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=update_step_1)
        t2 = threading.Thread(target=update_step_2)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        status = eng.get_execution_status(pb_id)
        steps = status.get("steps", [])
        step_statuses = {s.get("order"): s.get("status") for s in steps}

        assert step_statuses.get(1) == "completed", (
            f"Step 1 status lost: {step_statuses}"
        )
        assert step_statuses.get(2) == "skipped", (
            f"Step 2 status lost: {step_statuses}"
        )


class TestSaveExecutionPlanSemantics:
    """save_execution_plan must not silently wipe in-progress step states."""

    @pytest.fixture()
    def eng_with_progress(self, tmp_path):
        """Create an Engram with a plan that has step 1 completed."""
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Staged deploy",
            "description": "multi-step",
            "steps": [
                {"action": "Build", "detail": "compile"},
                {"action": "Test", "detail": "run suite"},
                {"action": "Ship", "detail": "push to prod"},
            ],
        })
        pbs = eng.get_playbooks(_update_access=False)
        pb_id = pbs[0]["id"]
        plan = eng.prepare_playbook_execution(pb_id)
        eng.save_execution_plan(plan)
        eng.update_execution_step(pb_id, 1, "completed", notes="built ok")
        return eng, pb_id, plan

    def test_save_does_not_wipe_existing_step_progress(self, eng_with_progress):
        """Calling save_execution_plan again must not reset completed steps."""
        eng, pb_id, original_plan = eng_with_progress

        # Verify step 1 is completed before the second save
        status_before = eng.get_execution_status(pb_id)
        step1_before = next(
            s for s in status_before["steps"] if s["order"] == 1
        )
        assert step1_before["status"] == "completed"

        # Second save with a fresh plan — this must NOT wipe step 1
        fresh_plan = eng.prepare_playbook_execution(pb_id)
        result = eng.save_execution_plan(fresh_plan)

        status_after = eng.get_execution_status(pb_id)
        step1_after = next(
            s for s in status_after["steps"] if s["order"] == 1
        )
        assert step1_after["status"] == "completed", (
            f"save_execution_plan wiped step 1 progress: "
            f"was 'completed', now '{step1_after['status']}'"
        )

    def test_concurrent_save_and_update_preserves_step(self, tmp_path):
        """A save_execution_plan concurrent with update_execution_step
        must not discard the step status update."""
        eng = Engram(root=tmp_path)
        eng.add_playbook({
            "title": "Concurrent deploy plan",
            "description": "for race test",
            "steps": [
                {"action": "Prepare", "detail": "setup"},
                {"action": "Execute", "detail": "run"},
            ],
        })
        pbs = eng.get_playbooks(_update_access=False)
        pb_id = pbs[0]["id"]
        plan = eng.prepare_playbook_execution(pb_id)
        eng.save_execution_plan(plan)

        barrier = threading.Barrier(2, timeout=5)
        errors: list[Exception] = []

        def do_save():
            try:
                barrier.wait()
                fresh = eng.prepare_playbook_execution(pb_id)
                eng.save_execution_plan(fresh)
            except Exception as e:
                errors.append(e)

        def do_update():
            try:
                barrier.wait()
                eng.update_execution_step(pb_id, 1, "completed", notes="done")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=do_save)
        t2 = threading.Thread(target=do_update)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Thread errors: {errors}"

        status = eng.get_execution_status(pb_id)
        step1 = next(
            (s for s in status["steps"] if s["order"] == 1), None
        )
        # Regardless of ordering, step 1 must be completed:
        # - If update ran last: it set completed.
        # - If save ran last: it must have preserved the completed status.
        assert step1 is not None
        assert step1["status"] == "completed", (
            f"Step 1 progress lost in save-vs-update race: '{step1['status']}'"
        )
