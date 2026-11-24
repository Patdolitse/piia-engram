"""A failed test reports what its throwaway store's hooks.log recorded (test-only).

Hook capture is fail-soft: a dropped event leaves only a closed-code line in
``<store>/logs/hooks.log``. The conftest report hook shows that tail next to a
failed assertion, so an intermittently missing spool file is diagnosable in CI.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_LINE = "2026-11-24T00:00:00 [hook_spool] crypto-timeout"
_HEADER = "hooks.log tail: engram-home/logs/hooks.log"


def test_missing_spool_assertion_child(tmp_path: Path):
    """Collected by the child session only; an expected spool file is missing."""
    if os.environ.get("ENGRAM_DIAGNOSTIC_CHILD") != "1":
        return
    store = tmp_path / "engram-home"
    (store / "logs").mkdir(parents=True)
    (store / "logs" / "hooks.log").write_text(_LINE + "\n", encoding="utf-8")
    queued = list((store / "hooks" / "spool").glob("*.jsonl"))
    assert len(queued) == 1


def test_missing_spool_assertion_reports_the_hooks_log_tail(tmp_path: Path):
    env = dict(os.environ, ENGRAM_DIAGNOSTIC_CHILD="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(_ROOT / "src"))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--basetemp", str(tmp_path / "child-basetemp"),
         str(Path(__file__)) + "::test_missing_spool_assertion_child"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(_ROOT), timeout=300,
    )
    assert result.returncode == 1, result.stdout[-2000:] + result.stderr[-2000:]
    assert "assert len(queued) == 1" in result.stdout
    assert _HEADER in result.stdout and _LINE in result.stdout


@pytest.mark.parametrize("when,failed", [("call", True), ("call", False), ("setup", True)])
def test_tail_is_attached_to_a_failed_call_report_only(tmp_path: Path, when, failed):
    import conftest

    store = tmp_path / "engram-home"
    (store / "logs").mkdir(parents=True)
    older = [f"2026-11-24T00:00:{second:02d} [claude_stop] capture failed (TimeoutError)"
             for second in range(conftest._HOOKS_LOG_TAIL_LINES)]
    (store / "logs" / "hooks.log").write_text(
        "\n".join(older + [_LINE]) + "\n", encoding="utf-8")
    # An unwritable logs folder makes the producer keep a sibling diagnostic instead.
    fallback = tmp_path.with_name(tmp_path.name + ".hooks.log")
    fallback.write_text("2026-11-24T00:00:00 [cursor_save] event publish failed (OSError)\n",
                        encoding="utf-8")
    report = SimpleNamespace(when=when, failed=failed, sections=[])
    wrapper = conftest.pytest_runtest_makereport(
        SimpleNamespace(funcargs={"tmp_path": tmp_path}), None)
    try:
        assert next(wrapper) is None
        with pytest.raises(StopIteration):
            wrapper.send(SimpleNamespace(get_result=lambda: report))
        # Only the newest lines are reported.
        expected = [] if not (when == "call" and failed) else [
            (_HEADER, "\n".join(older[1:] + [_LINE])),
            ("hooks.log tail: " + fallback.name,
             "2026-11-24T00:00:00 [cursor_save] event publish failed (OSError)")]
        assert sorted(report.sections) == sorted(expected)
    finally:
        fallback.unlink()