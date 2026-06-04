"""Deterministic / golden-snapshot guard for the MCIC continuity benchmark.

``test_mcic_benchmark.py`` asserts the benchmark's *structure* (10 scenarios,
metadata-only, expected categories). This module adds the *reproducibility*
guarantee the continuity evidence needs to be trustworthy:

- Two consecutive runs produce byte-for-byte identical payloads (no hidden
  timestamps, ordering, or temp-path leakage).
- The payload matches a committed, versioned golden file, so a silent change to
  what the benchmark reports fails the build instead of quietly shipping
  different evidence.

Regenerate the golden after an *intentional* benchmark change with::

    python - <<'PY'
    import json, tempfile, sys; from pathlib import Path
    sys.path.insert(0, "demos"); from mcic_benchmark import run_benchmark
    with tempfile.TemporaryDirectory() as d: p = run_benchmark(Path(d))
    Path("tests/snapshots/mcic_v1_golden.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    PY
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from demos.mcic_benchmark import render_markdown, run_benchmark  # noqa: E402

_GOLDEN = _ROOT / "tests" / "snapshots" / "mcic_v1_golden.json"


def test_benchmark_is_deterministic_across_runs(tmp_path):
    first = run_benchmark(tmp_path / "a")
    second = run_benchmark(tmp_path / "b")
    assert first == second
    # The isolated-root path must not leak into the (otherwise identical) payload.
    assert str(tmp_path) not in json.dumps(first, ensure_ascii=False)


def test_benchmark_matches_committed_golden(tmp_path):
    assert _GOLDEN.is_file(), "missing golden — regenerate per this module's docstring"
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    payload = run_benchmark(tmp_path / "g")
    assert payload == golden, (
        "MCIC benchmark payload drifted from the committed golden. "
        "If intentional, regenerate tests/snapshots/mcic_v1_golden.json."
    )


def test_golden_is_a_passing_metadata_only_payload():
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert golden["overall_passed"] is True
    assert golden["passed_count"] == golden["scenario_count"] == 10
    # Metadata-only sentinels must never appear in the committed evidence.
    blob = json.dumps(golden, ensure_ascii=False)
    assert "MCIC_SECRET_VALUE" not in blob
    assert "OLD_MCIC_SUPERSEDED_BODY" not in blob


def test_markdown_render_is_deterministic(tmp_path):
    payload = run_benchmark(tmp_path / "m")
    assert render_markdown(payload) == render_markdown(payload)
