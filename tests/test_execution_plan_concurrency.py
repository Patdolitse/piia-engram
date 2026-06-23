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
