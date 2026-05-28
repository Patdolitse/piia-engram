"""Tests for the v4.0 hybrid-search scaffold (src/piia_engram/search_index.py).

Covers the zero-dep core: RRF fusion + the FTS5 index + the graceful
vector fallback. The vector backend itself (sqlite-vec + FastEmbed) is an
optional extra and lands in a later increment; tests here assert the
degrade-gracefully behavior when it's absent.
"""

from __future__ import annotations

from piia_engram.search_index import (
    RRF_K,
    SearchIndex,
    _entry_document,
    _fts_match_expr,
    reciprocal_rank_fusion,
    vector_backend_available,
)


# ── RRF fusion (pure, no deps) ──────────────────────────────────────────


def test_rrf_empty():
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_rrf_single_list_preserves_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [i for i, _ in fused] == ["a", "b", "c"]


def test_rrf_score_math():
    # one list, k=60: ranks 1,2 -> 1/61, 1/62
    fused = dict(reciprocal_rank_fusion([["a", "b"]], k=60))
    assert abs(fused["a"] - 1 / 61) < 1e-12
    assert abs(fused["b"] - 1 / 62) < 1e-12


def test_rrf_agreement_beats_single_signal():
    """An id ranked decently by two signals should beat an id ranked #1
    by only one signal — the core value of fusion."""
    keyword = ["x", "shared"]      # shared is rank 2 here
    fts = ["shared", "y"]          # shared is rank 1 here
    fused = reciprocal_rank_fusion([keyword, fts])
    top = fused[0][0]
    assert top == "shared", f"expected 'shared' on top, got {fused}"


def test_rrf_tiebreak_is_deterministic():
    # 'a' and 'b' each appear once at rank 1 -> equal score -> id-sorted
    fused = reciprocal_rank_fusion([["b"], ["a"]])
    assert [i for i, _ in fused] == ["a", "b"]


def test_rrf_k_constant_default():
    assert RRF_K == 60


# ── FTS document + match expr helpers ───────────────────────────────────


def test_entry_document_concatenates_priority_fields():
    doc = _entry_document({"summary": "alpha", "domain": ["python", "search"]})
    assert "alpha" in doc and "python" in doc and "search" in doc


def test_fts_match_expr_is_punctuation_safe():
    # raw punctuation must not leak into the MATCH grammar
    expr = _fts_match_expr("ci/cd: pre-commit (hook)!")
    assert expr == '"ci" OR "cd" OR "pre" OR "commit" OR "hook"'


def test_fts_match_expr_empty_for_no_tokens():
    assert _fts_match_expr("!!! ???") == ""


# ── SearchIndex / FTS5 ──────────────────────────────────────────────────


def _idx(tmp_path):
    # force vector off so tests are deterministic regardless of installed extras
    return SearchIndex(tmp_path / "search_index.db", enable_vector=False)


def test_rebuild_counts_and_skips_idless(tmp_path):
    idx = _idx(tmp_path)
    n = idx.rebuild([
        {"id": "1", "summary": "pre-commit hook blocks secrets"},
        {"id": "2", "title": "vector search design"},
        {"summary": "no id here"},  # skipped
    ])
    assert n == 2


def test_fts_search_finds_match(tmp_path):
    idx = _idx(tmp_path)
    idx.rebuild([
        {"id": "1", "summary": "pre-commit hook blocks secrets"},
        {"id": "2", "title": "vector search design"},
    ])
    assert idx.fts_search("secrets") == ["1"]
    assert idx.fts_search("vector") == ["2"]


def test_fts_search_empty_query(tmp_path):
    idx = _idx(tmp_path)
    idx.rebuild([{"id": "1", "summary": "anything"}])
    assert idx.fts_search("") == []


def test_fts_search_before_build_returns_empty(tmp_path):
    # never built => no table => empty result, not a crash
    idx = _idx(tmp_path)
    assert idx.fts_search("anything") == []


def test_rebuild_is_idempotent(tmp_path):
    idx = _idx(tmp_path)
    idx.rebuild([{"id": "1", "summary": "first build"}])
    n = idx.rebuild([{"id": "9", "summary": "second build replaces"}])
    assert n == 1
    assert idx.fts_search("second") == ["9"]
    assert idx.fts_search("first") == []  # old doc gone


# ── vector layer: graceful when extra absent ────────────────────────────


def test_vector_backend_probe_returns_bool():
    assert isinstance(vector_backend_available(), bool)


def test_vector_search_disabled_returns_empty(tmp_path):
    idx = SearchIndex(tmp_path / "i.db", enable_vector=False)
    assert idx.vector_search("anything") == []


def test_hybrid_search_fuses_keyword_and_fts(tmp_path):
    idx = _idx(tmp_path)
    idx.rebuild([
        {"id": "1", "summary": "pre-commit hook blocks secrets"},
        {"id": "2", "summary": "vector search design notes"},
        {"id": "3", "summary": "unrelated entry"},
    ])
    # keyword scorer (external) thinks 2 then 1; FTS for 'secrets' returns 1
    fused = idx.hybrid_search("secrets", keyword_ranking=["2", "1"], limit=10)
    ids = [i for i, _ in fused]
    # 1 gets contributions from both signals -> should top the list
    assert ids[0] == "1"
    assert set(ids) == {"1", "2"}
