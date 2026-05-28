"""Integration tests for v4.0 hybrid search wired into Engram.search_knowledge.

Covers the index lifecycle (rebuild/freshness) and the search_knowledge
keyword-vs-hybrid behavior. Vector-dependent assertions are skip-guarded;
the FTS + keyword + lifecycle parts run without the [vector] extra.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.search_index import vector_backend_available

vec_only = pytest.mark.skipif(
    not vector_backend_available(), reason="[vector] extra not installed"
)


def _engram(tmp_path: Path) -> Engram:
    eng = Engram(root=tmp_path)
    eng.add_lesson({"summary": "use a pre-commit hook to block committing secrets and API keys",
                    "domain": "security"})
    eng.add_lesson({"summary": "reciprocal rank fusion blends multiple ranked result lists",
                    "domain": "search"})
    eng.add_decision({"title": "embedding model choice", "choice": "all-MiniLM-L6-v2",
                      "reasoning": "small and fast for local semantic vectors"})
    return eng


# ── index lifecycle ─────────────────────────────────────────────────────


def test_rebuild_index_counts_and_reports_vector(tmp_path):
    eng = _engram(tmp_path)
    res = eng.rebuild_index()
    assert res["indexed"] == 3
    assert isinstance(res["vector_enabled"], bool)
    assert (tmp_path / "search_index.db").exists()


def test_index_fingerprint_stable_until_content_changes(tmp_path):
    eng = _engram(tmp_path)
    entries = eng._all_indexable_entries()
    fp1 = eng._entries_fingerprint(entries)
    eng._ensure_index_fresh(entries)
    assert eng._hybrid_index().fingerprint() == fp1
    # adding a lesson changes the fingerprint -> triggers rebuild
    eng.add_lesson({"summary": "a brand new unrelated lesson about caching"})
    fp2 = eng._entries_fingerprint(eng._all_indexable_entries())
    assert fp2 != fp1


def test_fingerprint_changes_with_vector_backend_availability(tmp_path, monkeypatch):
    """v3.33.2: installing the [vector] extra after a FTS-only build flips
    backend availability False->True; the fingerprint must change so the
    index rebuilds and the vector signal actually gets built."""
    import piia_engram.search_index as si

    eng = _engram(tmp_path)
    entries = eng._all_indexable_entries()
    monkeypatch.setattr(si, "vector_backend_available", lambda: False)
    fp_off = eng._entries_fingerprint(entries)
    monkeypatch.setattr(si, "vector_backend_available", lambda: True)
    fp_on = eng._entries_fingerprint(entries)
    assert fp_off != fp_on


def test_hybrid_keeps_keyword_hits_at_small_limit(tmp_path, monkeypatch):
    """v3.33.2 recall guarantee: RRF reordering must not let truncation at
    `limit` evict a keyword hit — keyword result ⊆ hybrid result."""
    monkeypatch.delenv("ENGRAM_SEARCH", raising=False)
    eng = Engram(root=tmp_path)
    eng.add_lesson({"summary": "alpha pytest fixture scope guidance"})
    eng.add_lesson({"summary": "beta pytest parametrize data driven tests"})
    eng.add_lesson({"summary": "gamma pytest conftest shared fixtures"})
    q = "pytest fixture"
    kw_ids = {r["id"] for r in eng.search_knowledge(q, scope="lessons", limit=2)["lessons"]}
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    hy = eng.search_knowledge(q, scope="lessons", limit=2)["lessons"]
    hy_ids = {r["id"] for r in hy}
    assert kw_ids, "keyword path should return at least one hit"
    assert kw_ids <= hy_ids, "hybrid dropped a keyword hit at the limit"
    assert len(hy) <= 2


def test_has_vector_table_method_exists():
    from piia_engram.search_index import SearchIndex
    assert hasattr(SearchIndex, "has_vector_table")


def test_fingerprint_changes_when_embed_model_changes(tmp_path, monkeypatch):
    """Swapping the embedding model (content unchanged) MUST change the
    fingerprint so the index rebuilds — otherwise a stale vector table at
    the old dimension silently disables the vector signal (v3.33.1 fix)."""
    import piia_engram.search_index as si

    eng = _engram(tmp_path)
    entries = eng._all_indexable_entries()
    monkeypatch.setattr(si, "EMBED_MODEL", "model-A")
    fp_a = eng._entries_fingerprint(entries)
    monkeypatch.setattr(si, "EMBED_MODEL", "model-B")
    fp_b = eng._entries_fingerprint(entries)
    assert fp_a != fp_b


# ── search_knowledge: flag off (keyword) vs on (hybrid) ─────────────────


def test_keyword_path_default_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_SEARCH", raising=False)
    eng = _engram(tmp_path)
    res = eng.search_knowledge("secrets", scope="lessons")
    ids = [r.get("summary", "") for r in res["lessons"]]
    assert any("secrets" in s for s in ids)
    # keyword path: _score present, no _keyword_score key
    assert res["lessons"] and "_keyword_score" not in res["lessons"][0]


def test_hybrid_path_preserves_result_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _engram(tmp_path)
    res = eng.search_knowledge("secrets", scope="all")
    assert set(res.keys()) == {"lessons", "decisions", "playbooks"}
    hit = next((r for r in res["lessons"] if "secrets" in r.get("summary", "")), None)
    assert hit is not None
    assert "_score" in hit and "_keyword_score" in hit  # hybrid annotates both


def test_hybrid_recall_superset_of_keyword(tmp_path, monkeypatch):
    """Every id the keyword path returns must also appear in hybrid."""
    eng = _engram(tmp_path)
    monkeypatch.delenv("ENGRAM_SEARCH", raising=False)
    kw = eng.search_knowledge("fusion ranked lists", scope="lessons")
    kw_ids = {r["id"] for r in kw["lessons"]}
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    hy = eng.search_knowledge("fusion ranked lists", scope="lessons")
    hy_ids = {r["id"] for r in hy["lessons"]}
    assert kw_ids <= hy_ids


@vec_only
def test_hybrid_surfaces_semantic_match_keyword_misses(tmp_path, monkeypatch):
    """A query with no lexical overlap should still surface the semantically
    related lesson via the vector signal under hybrid."""
    monkeypatch.setenv("ENGRAM_SEARCH", "hybrid")
    eng = _engram(tmp_path)
    # 'combining several ordered candidate sets' ~ the RRF lesson, but shares
    # essentially no exact tokens with it.
    res = eng.search_knowledge("combining several ordered candidate sets", scope="lessons")
    summaries = [r.get("summary", "") for r in res["lessons"]]
    assert any("reciprocal rank fusion" in s for s in summaries)
