"""Tests for the synthetic store-integrity fault-injection harness.

These assert the harness's core promise: every injected corruption is *detected*
by the read-only integrity scan (the store fails loud, never silently serving
corrupt data), the scan never mutates the store, and a clean synthetic store is
reported healthy. They also pin the ledger-tamper regression: a broken
governance chain must surface ``ledger_chain_broken`` (previously masked by a
``bool(tuple)``-always-true bug in ``integrity._scan_ledger``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DEMOS = _ROOT / "demos"
if str(_DEMOS) not in sys.path:
    sys.path.insert(0, str(_DEMOS))

import store_integrity_harness as harness  # noqa: E402
from piia_engram import integrity, storage  # noqa: E402


def test_clean_synthetic_store_is_healthy(tmp_path: Path):
    root = harness.build_synthetic_store(tmp_path / "clean")
    report = integrity.scan_integrity(root)
    assert report["healthy"] is True
    assert report["problems"] == []


@pytest.mark.parametrize("name", list(harness._FAULTS.keys()))
def test_each_injected_fault_is_detected(tmp_path: Path, name: str):
    injector, expected_code = harness._FAULTS[name]
    result = harness._run_case(tmp_path, name, injector, expected_code)
    assert result["detected"] is True, f"{name}: expected {expected_code} not detected"
    assert result["store_unhealthy"] is True
    assert result["scan_read_only"] is True


def test_tampered_ledger_regression(tmp_path: Path):
    """A broken governance chain must NOT be silently reported healthy.

    Regression guard for the ``bool(ledger.verify())`` tuple bug: verify()
    returns ``(ok, message)``, so the bare-bool form was always truthy.
    """
    root = harness.build_synthetic_store(tmp_path / "ledger")
    # Sanity: clean ledger verifies.
    assert integrity.scan_integrity(root)["ledger"]["ok"] is True
    harness._inject_tampered_ledger(root)
    report = integrity.scan_integrity(root)
    assert report["ledger"]["ok"] is False
    assert report["healthy"] is False
    assert any(p["code"] == "ledger_chain_broken" for p in report["problems"])


def test_corrupt_active_store_is_not_silently_served(tmp_path: Path):
    """The read path fails loud (DataCorruptionError) instead of returning {}.

    Corruption must surface, not be swallowed into an empty/partial result.
    """
    root = harness.build_synthetic_store(tmp_path / "loud")
    lessons = root / "knowledge" / "lessons.json"
    lessons.write_text('[{"id": "L1", "summ', encoding="utf-8")  # truncated
    with pytest.raises(storage.DataCorruptionError):
        storage._read_json(lessons)
    # A backup copy is created so the bad bytes can be recovered manually.
    backups = list((root / "knowledge").glob("lessons.corrupt.*.json"))
    assert backups, "corrupt file should be quarantined to a .corrupt backup"


def test_harness_overall_passes_and_is_read_only(tmp_path: Path):
    report = harness.run_harness(tmp_path / "base")
    assert report["overall_passed"] is True
    assert report["all_detected"] is True
    assert report["all_unhealthy"] is True
    assert report["all_read_only"] is True
    assert report["control_healthy"] is True
    assert report["synthetic_only"] is True


def test_harness_report_is_metadata_only(tmp_path: Path):
    """The report must not leak synthetic knowledge bodies."""
    import json

    report = harness.run_harness(tmp_path / "meta")
    blob = json.dumps(report, ensure_ascii=False)
    assert "synthetic lesson one" not in blob
    assert "synthetic lesson two" not in blob
    for case in report["cases"]:
        assert set(case) >= {"fault", "expected_code", "detected", "store_unhealthy", "scan_read_only"}


def test_harness_does_not_touch_real_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The harness must operate only under the given base, never the real root.

    We point the default Engram root at a sentinel dir and assert the harness
    never creates it (it always passes explicit roots to the ledger/scan).
    """
    sentinel = tmp_path / "REAL_ENGRAM_MUST_NOT_BE_TOUCHED"
    monkeypatch.setenv("ENGRAM_DIR", str(sentinel))
    harness.run_harness(tmp_path / "iso_base")
    assert not sentinel.exists()
