"""Capacity refusals are reported to the caller, never turned into success."""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram import Engram

BASE_LIMITS = {
    "ENGRAM_CAP_SOFT": "2", "ENGRAM_CAP_HARD": "2",
    "ENGRAM_REVIEW_QUEUE_MAX": "2", "ENGRAM_REVIEW_QUEUE_CEILING": "3",
    "ENGRAM_REVIEW_MIN_STAY_DAYS": "7", "ENGRAM_RETIRED_GRACE_DAYS": "30",
    "ENGRAM_RETIRED_MAX": "5",
}


def _store(tmp_path: Path, monkeypatch, **overrides) -> tuple[Path, Engram]:
    for name, value in {**BASE_LIMITS, **{k: str(v) for k, v in overrides.items()}}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    root = tmp_path / "store"
    monkeypatch.setenv("ENGRAM_DIR", str(root))
    return root, Engram(root=root)


def _lesson(engram: Engram, text: str, tier: str, **extra) -> str:
    return engram.add_lesson({"summary": text, "tier": tier, **extra}, domain="cap")["id"]


def _full_reviewed_store(tmp_path: Path, monkeypatch) -> tuple[Path, Engram, list[str], str]:
    root, engram = _store(tmp_path, monkeypatch)
    reviewed = [_lesson(engram, f"reviewed lesson number {i} with enough words", "verified") for i in range(2)]
    staged = _lesson(engram, "unreviewed lesson that is waiting for review", "staging")
    return root, engram, reviewed, staged


def _active_bytes(root: Path) -> bytes:
    return (root / "knowledge" / "lessons.json").read_bytes()


def _audit_resources(root: Path) -> list[str]:
    path = root / "audit.log"
    if not path.is_file():
        return []
    return [json.loads(line).get("resource") for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_promote_at_the_hard_cap_reports_capacity_full_and_writes_nothing(tmp_path, monkeypatch):
    root, engram, _reviewed, staged = _full_reviewed_store(tmp_path, monkeypatch)
    before = _active_bytes(root)
    result = engram.promote_knowledge(staged)
    assert result["status"] == "capacity_full"
    assert _active_bytes(root) == before


def test_update_tier_at_the_hard_cap_is_refused_without_side_effects(tmp_path, monkeypatch):
    root, engram, _reviewed, staged = _full_reviewed_store(tmp_path, monkeypatch)
    before = _active_bytes(root)
    result = engram.update_knowledge(staged, {"tier": "verified"})
    assert result["error"] == "capacity_full"
    assert _active_bytes(root) == before
    assert "knowledge/tier_change" not in _audit_resources(root)


def test_onboard_accept_at_the_hard_cap_is_refused_without_audit(tmp_path, monkeypatch):
    root, engram, _reviewed, _staged = _full_reviewed_store(tmp_path, monkeypatch)
    candidate = engram.add_lesson(
        {"summary": "This project includes the file `README.md`.", "domain": "repo-fact",
         "tier": "staging", "provenance": {"anchor_ref": "file:README.md"}},
        _allow_internal_provenance=True,
    )["id"]
    before = _active_bytes(root)
    result = engram.accept_onboard_candidate(candidate)
    assert result["error"] == "capacity_full"
    assert _active_bytes(root) == before
    assert "knowledge/onboard-accept" not in _audit_resources(root)


def test_restore_to_verified_at_the_hard_cap_is_refused(tmp_path, monkeypatch):
    root, engram, reviewed, _staged = _full_reviewed_store(tmp_path, monkeypatch)
    assert engram.soft_archive_knowledge_tier(reviewed[0], allow_verified=True)["changed"] is True
    _lesson(engram, "another reviewed lesson that takes the free slot", "verified")
    before = _active_bytes(root)
    result = engram.restore_lifecycle_archive(reviewed[0])
    assert result["error"] == "capacity_full"
    assert _active_bytes(root) == before
    assert "knowledge/lifecycle_restore" not in _audit_resources(root)


def test_reactivating_a_row_into_a_full_queue_is_refused(tmp_path, monkeypatch):
    root, engram = _store(tmp_path, monkeypatch, ENGRAM_REVIEW_QUEUE_MAX=1, ENGRAM_REVIEW_QUEUE_CEILING=1)
    first = _lesson(engram, "first unreviewed lesson with enough words", "staging")
    assert "error" not in engram.update_knowledge(first, {"status": "outdated"})
    _lesson(engram, "second unreviewed lesson with enough words", "staging")
    before = _active_bytes(root)
    result = engram.update_knowledge(first, {"status": "active"})
    assert result["error"] == "review_queue_full"
    assert _active_bytes(root) == before


def test_a_refused_approval_in_a_review_batch_is_counted_as_failed(tmp_path, monkeypatch):
    from piia_engram.staging_review import batch_review_staging

    root, engram, _reviewed, staged = _full_reviewed_store(tmp_path, monkeypatch)
    before = _active_bytes(root)
    result = batch_review_staging(
        engram, [{"id": staged, "action": "approve"}], confirm=True, dry_run=False
    )
    assert result["counts"]["failed"] == 1
    assert result["counts"].get("applied", 0) == 0
    assert _active_bytes(root) == before
