"""Round-3: non-destructive semantic near-duplicate surfacing on write.

The lexical dedup (``_bigram_similarity``) already runs in
``add_lesson`` / ``add_decision``. This module covers the *additive*
semantic hook: when the lexical tier PASSES (bigram < 0.55) but a
semantic neighbor is close, the new item is still ADDED and merely
cross-linked (``related_ids`` + ``_dedup_note``). There is never a
semantic *reject* — a false positive must not lose knowledge.

These tests drive the write-hook and its gating without the real vector
backend by monkeypatching the neighbor source
(``Engram._semantic_neighbors_for_write``), so they run everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram.core import Engram


def _eng(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _load(tmp_path: Path, name: str) -> list[dict]:
    return json.loads((tmp_path / "knowledge" / name).read_text(encoding="utf-8"))


# ── write-hook: cross-link when lexical passes but semantic is close ──────


def test_lesson_semantic_crosslink_when_lexical_passes(tmp_path, monkeypatch):
    eng = _eng(tmp_path)
    first = eng.add_lesson("飞机降落前必须放下起落架", "aviation")
    # `second` shares essentially no CJK bigrams with `first` (lexical PASS),
    # but the (mocked) semantic source says they are near-duplicates.
    monkeypatch.setattr(
        eng, "_semantic_neighbors_for_write",
        lambda text, **kw: [(first["id"], 0.88)], raising=False,
    )
    second = eng.add_lesson("数据库连接池要设置最大空闲连接数", "backend")

    assert second.get("status") != "duplicate"          # never reject
    assert first["id"] in second.get("related_ids", [])  # forward link
    note = second.get("_dedup_note", "")
    assert "semantically related" in note
    assert first["id"] in note
    # bidirectional: the existing neighbor links back to the new item
    by_id = {l["id"]: l for l in _load(tmp_path, "lessons.json")}
    assert second["id"] in by_id[first["id"]].get("related_ids", [])


def test_lesson_semantic_never_rejects(tmp_path, monkeypatch):
    """Even at cos≈1.0 the item is ADDED — semantic signal never rejects."""
    eng = _eng(tmp_path)
    first = eng.add_lesson("飞机降落前必须放下起落架", "aviation")
    monkeypatch.setattr(
        eng, "_semantic_neighbors_for_write",
        lambda text, **kw: [(first["id"], 0.999)], raising=False,
    )
    second = eng.add_lesson("数据库连接池要设置最大空闲连接数", "backend")
    assert second.get("status") != "duplicate"
    assert len(eng.get_lessons()) == 2


def test_lesson_semantic_does_not_override_lexical_note(tmp_path, monkeypatch):
    """When the lexical tier already linked (>=0.55), the semantic hook must
    not fire or overwrite the lexical ``_dedup_note``."""
    eng = _eng(tmp_path)
    first = eng.add_lesson("Redis 缓存雪崩的解决方案", "backend")
    monkeypatch.setattr(
        eng, "_semantic_neighbors_for_write",
        lambda text, **kw: [(first["id"], 0.999)], raising=False,
    )
    # supplement marker demotes the >=0.95 match to the lexical "related" tier
    second = eng.add_lesson("Redis 缓存雪崩的解决方案（补充案例）", "backend")
    note = second.get("_dedup_note", "")
    assert note.startswith("related to")          # lexical note retained
    assert "semantically related" not in note     # semantic did NOT override


def test_lesson_semantic_skips_unverifiable_neighbor(tmp_path, monkeypatch):
    """A neighbor id that is not a current same-type active entry (stale index,
    cross-type, archived) must be dropped inside the lock — no link."""
    eng = _eng(tmp_path)
    eng.add_lesson("飞机降落前必须放下起落架", "aviation")
    monkeypatch.setattr(
        eng, "_semantic_neighbors_for_write",
        lambda text, **kw: [("nonexistent-id-xyz", 0.99)], raising=False,
    )
    second = eng.add_lesson("数据库连接池要设置最大空闲连接数", "backend")
    assert "_dedup_note" not in second
    assert second.get("related_ids", []) == []


def test_lesson_semantic_links_top1_only(tmp_path, monkeypatch):
    """Over-linking guard: only the single best verified neighbor is linked."""
    eng = _eng(tmp_path)
    a = eng.add_lesson("飞机降落前必须放下起落架", "aviation")
    b = eng.add_lesson("烤箱先预热到两百度再放入面包", "cooking")
    monkeypatch.setattr(
        eng, "_semantic_neighbors_for_write",
        lambda text, **kw: [(a["id"], 0.90), (b["id"], 0.85)], raising=False,
    )
    c = eng.add_lesson("数据库连接池要设置最大空闲连接数", "backend")
    assert c.get("related_ids", []) == [a["id"]]   # top-1 only
    assert b["id"] not in c.get("related_ids", [])


def test_decision_semantic_crosslink_when_lexical_passes(tmp_path, monkeypatch):
    eng = _eng(tmp_path)
    first = eng.add_decision("数据库迁移策略怎么选", "先备份再迁移", "降低恢复风险")
    monkeypatch.setattr(
        eng, "_semantic_neighbors_for_write",
        lambda text, **kw: [(first["id"], 0.87)], raising=False,
    )
    second = eng.add_decision("前端框架用哪个", "选 React", "团队熟悉度高")

    assert second.get("status") != "duplicate"
    assert first["id"] in second.get("related_ids", [])
    note = second.get("_dedup_note", "")
    assert "semantically related" in note and first["id"] in note
    by_id = {d["id"]: d for d in _load(tmp_path, "decisions.json")}
    assert second["id"] in by_id[first["id"]].get("related_ids", [])


# ── gating: the hook source returns [] unless hybrid+backend+not-encrypted ─


def test_semantic_source_off_by_default(tmp_path, monkeypatch):
    """ENGRAM_SEARCH unset (keyword default) → no neighbors, zero behavior change."""
    monkeypatch.delenv("ENGRAM_SEARCH", raising=False)
    eng = _eng(tmp_path)
    assert eng._semantic_neighbors_for_write("anything") == []


def test_semantic_source_skips_when_corpus_encrypted(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _eng(tmp_path)
    monkeypatch.setattr(eng, "_corpus_encrypted", lambda: True)
    assert eng._semantic_neighbors_for_write("anything") == []


def test_semantic_source_skips_when_backend_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _eng(tmp_path)
    monkeypatch.setattr(eng, "_corpus_encrypted", lambda: False)
    import piia_engram.retrieval as r
    monkeypatch.setattr(r, "vector_backend_available", lambda: False, raising=False)
    assert eng._semantic_neighbors_for_write("anything") == []


def test_semantic_write_under_encryption_creates_no_index(tmp_path, monkeypatch):
    """Regression guard: a write under corpus encryption must never
    materialise search_index.db, even with hybrid enabled."""
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _eng(tmp_path)
    monkeypatch.setattr(eng, "_corpus_encrypted", lambda: True)
    eng.add_lesson("飞机降落前必须放下起落架", "aviation")
    assert not (tmp_path / "search_index.db").exists()


def test_semantic_source_excludes_own_id(tmp_path, monkeypatch):
    """With all gates open, the to-be-written item's own id is filtered out of
    its neighbor list (defensive: it is not in the index at call time, but the
    method guards anyway). Drives the ``exclude_id`` branch directly."""
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _eng(tmp_path)
    monkeypatch.setattr(eng, "_corpus_encrypted", lambda: False)
    import piia_engram.retrieval as r
    monkeypatch.setattr(r, "vector_backend_available", lambda: True, raising=False)

    class _FakeIndex:
        def semantic_neighbors(self, text, limit=5, min_similarity=0.0):
            return [("self-id", 0.99), ("other-id", 0.80)]

    monkeypatch.setattr(eng, "_hybrid_index", lambda: _FakeIndex())
    out = eng._semantic_neighbors_for_write("anything", exclude_id="self-id")
    assert ("other-id", 0.80) in out
    assert all(eid != "self-id" for eid, _ in out)
