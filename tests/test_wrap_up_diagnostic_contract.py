from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "diagnose_wrap_up_session.py"


def test_diagnostic_defaults_to_isolated_store() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "wrap_up_session_diagnostic.v1"
    assert payload["completed"] is True
    assert payload["store_mode"] == "isolated"
    assert payload["live_store"] is False
    assert payload["writeful"] is False
    assert payload["daily_log"]["checked"] is True
    assert payload["daily_log"]["written"] is True
    assert "file" not in payload["daily_log"]
    assert payload["maintenance"]["reconcile_memories"]["status"] == "skipped"
    assert payload["maintenance"]["reconcile_ai_configs"]["status"] == "skipped"
    assert isinstance(payload["timing"]["total_ms"], int)


def test_diagnostic_live_write_requires_two_explicit_flags() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "--live-inspect" in text
    assert "--live-closeout" in text
    assert "--allow-write" in text
    assert "live_store" in text
    assert "TemporaryDirectory" in text
    assert "run_reconcile=False" in text


def test_live_closeout_without_allow_write_is_refused() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--live-closeout"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--allow-write" in result.stderr


def test_diagnostic_output_redacts_paths() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--synthetic-error"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr

    assert "Workspace With Spaces" not in body
    assert "secret.json" not in body
    assert "<path>" in body


def test_diagnostic_compare_fast_outputs_two_modes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--compare-fast"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "wrap_up_session_compare.v1"
    assert payload["live_store"] is False
    assert payload["standard"]["store_mode"] == "isolated"
    assert payload["standard"]["live_store"] is False
    assert payload["standard"]["writeful"] is False
    assert payload["standard"]["maintenance"]["closeout_mode"] == "standard"
    assert payload["fast"]["store_mode"] == "isolated"
    assert payload["fast"]["live_store"] is False
    assert payload["fast"]["writeful"] is False
    assert payload["fast"]["maintenance"]["closeout_mode"] == "fast"
    assert payload["fast"]["maintenance"]["extract_session_insights"]["status"] == "skipped"


def test_diagnostic_compare_fast_redacts_paths() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--compare-fast", "--synthetic-error"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    body = result.stdout + result.stderr

    assert "Workspace With Spaces" not in body
    assert "secret.json" not in body
    assert "<path>" in body


def test_diagnostic_classifies_tool_boundary_timeout() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--timeout-ms",
            "20",
            "--synthetic-delay-ms",
            "200",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    payload = json.loads(result.stdout)
    body = result.stdout + result.stderr

    assert payload["schema"] == "wrap_up_session_diagnostic.v1"
    assert payload["completed"] is False
    assert payload["timeout"]["status"] == "timed_out"
    assert payload["timeout"]["boundary"] == "diagnostic_tool"
    assert payload["timeout"]["timeout_ms"] == 20
    assert payload["daily_log"]["checked"] is True
    assert payload["daily_log"]["written"] is False
    assert "engram-wrapup-diagnostic-" not in body


def test_text_output_surfaces_timeout_classification() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--timeout-ms",
            "20",
            "--synthetic-delay-ms",
            "200",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert "completed=False" in result.stdout
    assert "timeout=timed_out" in result.stdout
    assert "boundary=diagnostic_tool" in result.stdout
    assert "daily_log_written=False" in result.stdout


def test_live_closeout_does_not_use_background_timeout_boundary(tmp_path: Path) -> None:
    env = {**os.environ, "ENGRAM_DIR": str(tmp_path / "live-store")}
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "--live-closeout",
            "--allow-write",
            "--timeout-ms",
            "20",
            "--synthetic-delay-ms",
            "80",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    payload = json.loads(result.stdout)

    assert payload["store_mode"] == "live"
    assert payload["writeful"] is True
    assert payload["completed"] is True
    assert payload["timeout"]["status"] == "not_applied"
    assert payload["timeout"]["boundary"] == "live_closeout"
    assert payload["daily_log"]["written"] is True
