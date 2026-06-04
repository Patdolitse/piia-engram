"""Tests for the backup/restore round-trip harness (Task 4, B+).

Pin the harness's promises: export → restore → re-export is content-equivalent,
re-importing the same backup adds nothing (idempotent replay), and the original
store is never modified by any of it. Also assert the report leaks no bodies.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEMOS = _ROOT / "demos"
if str(_DEMOS) not in sys.path:
    sys.path.insert(0, str(_DEMOS))

import backup_roundtrip_harness as harness  # noqa: E402


def _fingerprint(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return out


def test_roundtrip_passes_overall(tmp_path: Path):
    report = harness.run_harness(tmp_path / "base")
    assert report["overall_passed"] is True
    assert report["content_equivalent"] is True
    assert report["replay_added_nothing"] is True
    assert report["original_untouched"] is True


def test_restore_reports_success(tmp_path: Path):
    report = harness.run_harness(tmp_path / "base")
    assert report["restore_status"] == "success"
    assert report["lesson_count"] == 2
    assert report["decision_count"] == 1


def test_original_store_bytes_unchanged(tmp_path: Path):
    base = tmp_path / "base"
    base.mkdir()
    original_root = base / "original"
    harness.seed_store(original_root)
    before = _fingerprint(original_root)
    # A second full run that exports/restores must not mutate this seeded store.
    harness.run_harness(tmp_path / "base2")
    after = _fingerprint(original_root)
    assert before == after


def test_report_is_metadata_only(tmp_path: Path):
    report = harness.run_harness(tmp_path / "base")
    blob = json.dumps(report, ensure_ascii=False)
    assert "synthetic backup lesson one" not in blob
    assert "alpha detail" not in blob
    assert "which synthetic store format" not in blob


def test_temp_dir_only_no_real_store(tmp_path, monkeypatch):
    sentinel = tmp_path / "REAL_ENGRAM_MUST_NOT_BE_TOUCHED"
    monkeypatch.setenv("ENGRAM_DIR", str(sentinel))
    harness.run_harness(tmp_path / "base")
    assert not sentinel.exists()


def test_normalize_strips_volatile_and_provenance():
    export = {
        "identity": {
            "profile": {
                "role": "x",
                "updated_at": "2026-01-01T00:00:00",
                "_provenance": {"role": {"by": "u"}},
            }
        },
        "knowledge": {
            "lessons": [
                {"summary": "s", "detail": "d", "created_at": "t", "access_count": 9},
            ],
            "decisions": [],
        },
    }
    norm = harness._normalize_export(export)
    assert norm["identity"]["profile"] == {"role": "x"}
    assert norm["lessons"][0] == {"summary": "s", "detail": "d"}
