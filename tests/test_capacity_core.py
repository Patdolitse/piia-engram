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
