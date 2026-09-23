"""Capacity core: the locked lessons/decisions writer and its guard."""

from __future__ import annotations

import time
from pathlib import Path

import portalocker
import pytest

from piia_engram import storage


# -- knowledge write guard ---------------------------------------------------


def test_direct_write_to_a_knowledge_file_is_refused_in_tests(tmp_path: Path):
    target = tmp_path / "knowledge" / "lessons.json"
    with pytest.raises(storage.UnguardedKnowledgeWrite):
        storage._write_json(target, [])
    with pytest.raises(storage.UnguardedKnowledgeWrite):
        storage._update_json(tmp_path / "knowledge" / "decisions.json", lambda current: [], default=[])


def test_guarded_write_to_a_knowledge_file_is_allowed(tmp_path: Path):
    target = tmp_path / "knowledge" / "lessons.json"
    with storage.knowledge_write_allowed():
        storage._write_json(target, [{"id": "a"}])
    assert storage._read_json(target) == [{"id": "a"}]


def test_other_files_are_not_guarded(tmp_path: Path):
    storage._write_json(tmp_path / "knowledge" / "relations.json", [])
    storage._write_json(tmp_path / "other" / "lessons.json", [])


def test_non_blocking_update_skips_when_the_lock_is_held(tmp_path: Path):
    target = tmp_path / "data" / "file.json"
    target.parent.mkdir(parents=True)
    calls = []
    with portalocker.Lock(target.parent / ".engram-write.lock", "a", timeout=5):
        started = time.monotonic()
        result = storage._update_json(
            target, lambda current: calls.append(1) or {"x": 1}, default={}, blocking=False
        )
        elapsed = time.monotonic() - started
    assert result is None
    assert calls == []
    assert elapsed < 1.0
    assert not target.exists()


# -- capacity core through Engram --------------------------------------------

import hashlib
import json

from piia_engram import Engram
from piia_engram import core as core_mod
from piia_engram.storage import _read_json

KINDS = ("lesson", "decision")


def _limits(monkeypatch, *, min_stay=0, grace=0):
    for name, value in {
        "ENGRAM_CAP_SOFT": 6, "ENGRAM_CAP_HARD": 8,
        "ENGRAM_REVIEW_QUEUE_MAX": 3, "ENGRAM_REVIEW_QUEUE_CEILING": 5,
        "ENGRAM_REVIEW_MIN_STAY_DAYS": min_stay, "ENGRAM_RETIRED_GRACE_DAYS": grace,
        "ENGRAM_RETIRED_MAX": 3,
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)


def _words(i: int, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}-{i}".encode()).hexdigest()
    return " ".join("w" + digest[k:k + 7] for k in range(0, 56, 7))


def _add(engram: Engram, kind: str, i: int, salt: str, **extra) -> dict:
    if kind == "lesson":
        row = {"summary": _words(i, salt), **extra}
        return engram.add_lesson(row, domain="cap-test")
    row = {"question": _words(i, salt), "choice": _words(i, salt + "-c"), **extra}
    return engram.add_decision(row)


def _active(root: Path, kind: str) -> list[dict]:
    path = root / "knowledge" / f"{kind}s.json"
    return _read_json(path) if path.is_file() else []


def _archive(root: Path, kind: str) -> list[dict]:
    path = root / "knowledge" / "overflow_archive" / f"{kind}s.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("kind", KINDS)
def test_verified_rows_are_never_moved_by_capacity(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, kind, i, "V", tier="verified")["id"] for i in range(8)]
    assert [r["id"] for r in _active(tmp_path, kind)] == ids
    assert _archive(tmp_path, kind) == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_verified_write_beyond_the_hard_cap_goes_to_the_review_queue(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    for i in range(8):
        _add(engram, kind, i, "V", tier="verified")
    ninth = _add(engram, kind, 8, "V")
    assert ninth["tier"] == "staging"
    assert ninth["approval_reason"] == "capacity"
    assert sum(1 for r in _active(tmp_path, kind) if r["tier"] == "verified") == 8


@pytest.mark.parametrize("kind", KINDS)
def test_queue_rows_beyond_the_quota_move_to_the_archive(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, kind, i, "Q", tier="staging")["id"] for i in range(3)]
    fourth = _add(engram, kind, 3, "Q", tier="staging")
    assert fourth["overflow_archived_ids"] == [ids[0]]
    archived = _archive(tmp_path, kind)
    assert [(r["id"], r["overflow_archive_reason"]) for r in archived] == [(ids[0], "review_queue_quota")]
    assert [r["id"] for r in _active(tmp_path, kind)] == ids[1:] + [fourth["id"]]


@pytest.mark.parametrize("kind", KINDS)
def test_a_full_queue_places_the_new_row_in_the_archive(tmp_path, monkeypatch, kind):
    _limits(monkeypatch, min_stay=7)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, kind, i, "F", tier="staging")["id"] for i in range(5)]
    sixth = _add(engram, kind, 5, "F", tier="staging")
    assert sixth["placement"] == "archived"
    assert sixth["overflow_archived_ids"] == [sixth["id"]]
    assert [r["id"] for r in _active(tmp_path, kind)] == ids
    assert [(r["id"], r["overflow_archive_reason"]) for r in _archive(tmp_path, kind)] == [
        (sixth["id"], "review_queue_full")
    ]


@pytest.mark.parametrize("kind", KINDS)
def test_each_move_is_audited_with_id_and_reason_only(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = Engram(root=tmp_path)
    ids = [_add(engram, kind, i, "A", tier="staging")["id"] for i in range(4)]
    lines = (tmp_path / "audit.log").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line]
    archive_events = [e for e in events if e.get("action") == "archive"]
    assert [(e["resource"], e["detail"]) for e in archive_events] == [
        (f"knowledge/{kind}s", f"review_queue_quota id={ids[0]}")
    ]


def test_a_dropped_row_is_archived_as_removed(tmp_path, monkeypatch):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, "lesson", i, "D", tier="verified")["id"] for i in range(3)]
    path = tmp_path / "knowledge" / "lessons.json"
    engram._update_entries(path, "lesson", lambda rows: [r for r in rows if r["id"] != ids[1]])
    assert [r["id"] for r in _active(tmp_path, "lesson")] == [ids[0], ids[2]]
    assert [(r["id"], r["overflow_archive_reason"]) for r in _archive(tmp_path, "lesson")] == [(ids[1], "removed")]


def test_a_whole_file_write_archives_the_rows_it_drops(tmp_path, monkeypatch):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, "lesson", i, "W", tier="verified")["id"] for i in range(3)]
    kept = [r for r in _active(tmp_path, "lesson") if r["id"] != ids[1]]
    engram._write_entries(tmp_path / "knowledge" / "lessons.json", kept, "lesson")
    assert [r["id"] for r in _active(tmp_path, "lesson")] == [ids[0], ids[2]]
    assert [(r["id"], r["overflow_archive_reason"]) for r in _archive(tmp_path, "lesson")] == [(ids[1], "removed")]


@pytest.mark.parametrize("kind", KINDS)
def test_whole_file_rows_get_no_new_row_exemption_from_the_queue_quota(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    rows = [
        {"id": f"imp-{i}", "summary": _words(i, "I"), "question": _words(i, "I"), "choice": "c",
         "tier": "staging", "status": "active", "created_at": f"2026-01-0{i + 1}T00:00:00Z"}
        for i in range(5)
    ]
    engram._write_entries(tmp_path / "knowledge" / f"{kind}s.json", rows, kind)
    assert [r["id"] for r in _active(tmp_path, kind)] == ["imp-2", "imp-3", "imp-4"]
    assert [(r["id"], r["overflow_archive_reason"]) for r in _archive(tmp_path, kind)] == [
        ("imp-0", "review_queue_quota"), ("imp-1", "review_queue_quota")
    ]
    assert all(r["queued_at"].startswith("2026-01-0") for r in _active(tmp_path, kind))


@pytest.mark.parametrize("kind", KINDS)
def test_the_archive_is_written_before_the_active_file(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, kind, i, "O", tier="staging")["id"] for i in range(3)]
    real_update_json = core_mod._update_json
    active_path = tmp_path / "knowledge" / f"{kind}s.json"

    def _fail_active_write(path, mutator, **kwargs):
        if path == active_path:
            mutator(_read_json(path))
            raise OSError("simulated failure before the active file is replaced")
        return real_update_json(path, mutator, **kwargs)

    monkeypatch.setattr(core_mod, "_update_json", _fail_active_write)
    with pytest.raises(OSError):
        _add(engram, kind, 3, "O", tier="staging")
    monkeypatch.setattr(core_mod, "_update_json", real_update_json)
    assert ids[0] in [r["id"] for r in _active(tmp_path, kind)]
    assert ids[0] in [r["id"] for r in _archive(tmp_path, kind)]


@pytest.mark.parametrize("kind", KINDS)
def test_a_failed_archive_append_fails_the_whole_write(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    ids = [_add(engram, kind, i, "X", tier="staging")["id"] for i in range(3)]

    def _no_space(path, lines):
        raise OSError("simulated full disk")

    monkeypatch.setattr(core_mod, "_append_jsonl_lines", _no_space)
    with pytest.raises(OSError):
        _add(engram, kind, 3, "X", tier="staging")
    assert [r["id"] for r in _active(tmp_path, kind)] == ids
    assert _archive(tmp_path, kind) == []


# -- reads never block on the knowledge write lock -----------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_reads_do_not_wait_for_the_knowledge_write_lock(tmp_path, monkeypatch, kind):
    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    _add(engram, kind, 0, "R", tier="verified")
    lister = engram.get_lessons if kind == "lesson" else engram.get_decisions
    with portalocker.Lock(tmp_path / "knowledge" / ".engram-write.lock", "a", timeout=5):
        started = time.monotonic()
        rows = lister()
        elapsed = time.monotonic() - started
    assert len(rows) == 1
    assert elapsed < 1.0


def test_a_legacy_row_read_does_not_wait_for_the_lock(tmp_path, monkeypatch):
    from knowledge_seed import raw_write_json

    _limits(monkeypatch)
    engram = Engram(root=tmp_path)
    raw_write_json(tmp_path / "knowledge" / "lessons.json", [{"summary": "legacy row without fields"}])
    with portalocker.Lock(tmp_path / "knowledge" / ".engram-write.lock", "a", timeout=5):
        started = time.monotonic()
        rows = engram.get_lessons(_update_access=False)
        elapsed = time.monotonic() - started
    assert len(rows) == 1
    assert elapsed < 1.0
