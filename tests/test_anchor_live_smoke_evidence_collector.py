from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_anchor_live_smoke_evidence.py"


def test_collector_outputs_public_safe_aggregate_schema() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--synthetic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["schema"] == "anchor_live_smoke_evidence.v1"
    assert payload["public_safe"] is True
    assert {"checked", "valid", "invalid", "unknown", "superseded"} <= set(payload["anchors"])
    assert {"runs", "passed", "failed"} <= set(payload["live_smoke"])


def test_collector_does_not_emit_raw_paths_or_memory_bodies() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--synthetic"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    body = result.stdout

    assert "E:\\" not in body
    assert "C:\\" not in body
    assert "/Users/" not in body
    assert "raw_memory" not in body
    assert "secret" not in body.lower()


def test_live_aggregate_mode_requires_owner_flag() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--live"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--allow-live" in result.stderr
