"""Tests for scripts/metrics.py --weekly traction digest (M0 G5 / #81).

The weekly digest computes week-over-week deltas (GitHub stars, PyPI weekly
downloads) and the remaining gap to the #78 hard gate (500 stars AND 1000 PyPI
weekly downloads). All tests isolate ENGRAM_DIR to a temp dir and never hit the
network — fetch_* results are passed in directly or the live fetchers are
monkeypatched.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "metrics.py"


@pytest.fixture(scope="module")
def metrics():
    spec = importlib.util.spec_from_file_location("engram_metrics", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _isolate_engram_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    yield


def _seed_log(metrics, entries: list[dict]) -> None:
    """Write JSONL log entries into the isolated ENGRAM_DIR."""
    path = metrics.log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def test_engram_dir_respects_env(metrics, tmp_path: Path):
    assert metrics.engram_dir() == tmp_path
    assert metrics.log_file() == tmp_path / "metrics_log.jsonl"


def test_digest_without_baseline_has_no_deltas(metrics):
    gh = {"stars": 120}
    pypi = {"pypi_downloads_last_week": 300}

    digest = metrics.build_weekly_digest(gh, pypi)

    assert digest["window"]["has_baseline"] is False
    assert digest["stars"]["current"] == 120
    assert digest["stars"]["delta"] is None
    assert digest["pypi_weekly"]["delta"] is None
    # Gaps are still computed from current values.
    assert digest["gate_78"]["stars_gap"] == 500 - 120
    assert digest["gate_78"]["pypi_weekly_gap"] == 1000 - 300
    assert digest["gate_78"]["met"] is False


def test_digest_with_baseline_computes_deltas(metrics):
    now = datetime.now(timezone.utc)
    week_ago = (now - timedelta(days=7)).isoformat()
    _seed_log(metrics, [
        {"timestamp": week_ago,
         "github": {"stars": 100},
         "pypi": {"pypi_downloads_last_week": 250},
         "local": {}},
    ])

    gh = {"stars": 137}
    pypi = {"pypi_downloads_last_week": 410}
    digest = metrics.build_weekly_digest(gh, pypi)

    assert digest["window"]["has_baseline"] is True
    assert digest["stars"]["previous"] == 100
    assert digest["stars"]["delta"] == 37
    assert digest["pypi_weekly"]["previous"] == 250
    assert digest["pypi_weekly"]["delta"] == 160
    assert 6.0 <= digest["window"]["days"] <= 8.0


def test_baseline_picks_entry_closest_to_seven_days(metrics):
    now = datetime.now(timezone.utc)
    _seed_log(metrics, [
        {"timestamp": (now - timedelta(days=1)).isoformat(),
         "github": {"stars": 130}, "pypi": {"pypi_downloads_last_week": 390}, "local": {}},
        {"timestamp": (now - timedelta(days=7)).isoformat(),
         "github": {"stars": 100}, "pypi": {"pypi_downloads_last_week": 250}, "local": {}},
        {"timestamp": (now - timedelta(days=30)).isoformat(),
         "github": {"stars": 40}, "pypi": {"pypi_downloads_last_week": 80}, "local": {}},
    ])

    digest = metrics.build_weekly_digest({"stars": 140}, {"pypi_downloads_last_week": 420})

    # The ~7-day-old entry is the baseline, not the 1-day or 30-day ones.
    assert digest["stars"]["previous"] == 100
    assert digest["stars"]["delta"] == 40


def test_gate_met_when_both_thresholds_reached(metrics):
    digest = metrics.build_weekly_digest(
        {"stars": 500}, {"pypi_downloads_last_week": 1000})
    assert digest["gate_78"]["stars_gap"] == 0
    assert digest["gate_78"]["pypi_weekly_gap"] == 0
    assert digest["gate_78"]["met"] is True


def test_gate_not_met_when_only_one_threshold_reached(metrics):
    digest = metrics.build_weekly_digest(
        {"stars": 600}, {"pypi_downloads_last_week": 500})
    assert digest["gate_78"]["stars_gap"] == 0
    assert digest["gate_78"]["pypi_weekly_gap"] == 500
    assert digest["gate_78"]["met"] is False


def test_missing_metrics_yield_none_gaps(metrics):
    digest = metrics.build_weekly_digest({}, {})
    assert digest["stars"]["current"] is None
    assert digest["gate_78"]["stars_gap"] is None
    assert digest["gate_78"]["pypi_weekly_gap"] is None
    assert digest["gate_78"]["met"] is False


def test_weekly_report_json_output_is_parseable(metrics, capsys):
    digest = metrics.weekly_report(
        {"stars": 200}, {"pypi_downloads_last_week": 600}, as_json=True)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["stars"]["current"] == 200
    assert parsed["gate_78"]["pypi_weekly_gap"] == 400
    # Return value matches printed structure.
    assert parsed["gate_78"]["stars_gap"] == digest["gate_78"]["stars_gap"]


def test_weekly_report_text_output_mentions_gate(metrics, capsys):
    metrics.weekly_report({"stars": 200}, {"pypi_downloads_last_week": 600}, as_json=False)
    out = capsys.readouterr().out
    assert "#78" in out
    assert "500" in out and "1000" in out
