from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bench_wrap_up_session.py"


def test_benchmark_script_is_local_only_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "TemporaryDirectory" in text
    assert "run_reconcile=False" in text
    assert "not a public performance claim" in text
    assert "ENGRAM_TELEMETRY" in text
    assert "ENGRAM_FEEDBACK" in text


def test_benchmark_script_json_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--samples", "1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "wrap_up_session_timing_benchmark.v1"
    assert payload["samples"][0]["maintenance"]["reconcile_memories"] == "skipped"
    assert payload["samples"][0]["maintenance"]["reconcile_ai_configs"] == "skipped"
    assert isinstance(payload["samples"][0]["timing"]["total_ms"], int)
    assert "DATA FRAGMENTATION" not in result.stderr
