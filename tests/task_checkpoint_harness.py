"""Test-only capture-success seam; production never imports this module.

Success contracts run the actual bounded reader/parser synchronously, avoiding
the best-effort scheduling deadline. A generous outer input/publication budget
belongs only to these tests. Deadline tests use the unchanged production path.
No environment variable, hook payload or transcript can select this seam.
"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import runpy
import sys
import time
from unittest.mock import patch


def snapshot(transcript: str, deadline: float, capture_event: str = "Stop"):
    import os
    from piia_engram.task_checkpoint import _read_latest
    return _read_latest(transcript, os.environ.get("ENGRAM_HOOK_TRANSCRIPT_ROOTS", ""), capture_event)


def install_capture_success(monkeypatch):
    from piia_engram.hooks import _producer
    monkeypatch.setattr(_producer, "_task_snapshot", snapshot)
    monkeypatch.setattr(_producer, "INPUT_BUDGET_SECONDS", 60.0)


def hook_command(*, read_delay: float = 0.0) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), str(read_delay)]


def main():
    # Explicit test-process entry point, never reached by a product hook.
    from piia_engram import task_checkpoint
    from piia_engram.hooks import _producer
    delay, module, *args = sys.argv[1:]
    if module != "auto_save_on_stop":
        raise ValueError("capture-success harness only runs the Stop hook")
    original = task_checkpoint._read_latest
    def read(*args):
        time.sleep(float(delay))
        return original(*args)
    sys.argv = [module, *args]
    with ExitStack() as stack:
        stack.enter_context(patch.object(_producer, "_task_snapshot", snapshot))
        stack.enter_context(patch.object(_producer, "INPUT_BUDGET_SECONDS", 60.0))
        stack.enter_context(patch.object(task_checkpoint, "_read_latest", read))
        runpy.run_module("piia_engram.hooks." + module, run_name="__main__")


if __name__ == "__main__":
    main()
