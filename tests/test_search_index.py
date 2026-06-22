"""Tests for the v4.0 hybrid-search scaffold (src/piia_engram/search_index.py).

Covers the zero-dep core: RRF fusion + the FTS5 index + the graceful
vector fallback. The vector backend itself (sqlite-vec + FastEmbed) is an
optional extra and lands in a later increment; tests here assert the
degrade-gracefully behavior when it's absent.
"""

from __future__ import annotations

import sqlite3

import pytest

from piia_engram.search_index import (
    RRF_K,
    SearchIndex,
    _entry_document,
    _fts_match_expr,
    reciprocal_rank_fusion,
    vector_backend_available,
)

vec_only = pytest.mark.skipif(
    not vector_backend_available(), reason="[vector] extra not installed"
)


def _sqlite_vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401

        return True
    except Exception:
        return False


# The cosine-math / ordering tests only need the sqlite-vec extension (the
# query embedding is monkeypatched), NOT the heavy FastEmbed model. Gating on
# sqlite-vec alone lets them run wherever the extension is installed.
vec_ext_only = pytest.mark.skipif(
    not _sqlite_vec_available(), reason="sqlite_vec extension not installed"
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


# ── vector layer (requires the [vector] extra) ──────────────────────────


def _vidx(tmp_path):
    return SearchIndex(tmp_path / "search_index.db", enable_vector=True)


@vec_only
def test_vector_search_ranks_semantic_over_unrelated(tmp_path):
    """Semantic match wins even with no lexical overlap."""
    idx = _vidx(tmp_path)
    idx.rebuild([
        {"id": "sec", "summary": "blocking leaked credentials and secret tokens before a commit"},
        {"id": "weather", "summary": "a sunny day at the beach with warm sand"},
    ])
    res = idx.vector_search("preventing passwords and API keys from leaking", limit=2)
    assert "sec" in res and "weather" in res
    assert res.index("sec") < res.index("weather")


@vec_only
def test_vector_incremental_reembeds_changed_and_drops_removed(tmp_path):
    db = tmp_path / "search_index.db"
    idx = SearchIndex(db, enable_vector=True)
    idx.rebuild([
        {"id": "a", "summary": "alpha topic about databases"},
        {"id": "b", "summary": "beta topic about cooking recipes"},
    ])

    def vec_eids():
        con = sqlite3.connect(str(db))
        try:
            return {r[0] for r in con.execute("SELECT eid FROM vec_map")}
        finally:
            con.close()

    assert vec_eids() == {"a", "b"}
    # drop b, change a's content
    idx.rebuild([{"id": "a", "summary": "alpha topic about distributed databases and sharding"}])
    assert vec_eids() == {"a"}
    # vec table and map stay consistent (no orphan rows). Querying the vec0
    # virtual table requires the extension loaded on this connection.
    import sqlite_vec

    con = sqlite3.connect(str(db))
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        n_vec = con.execute("SELECT COUNT(*) FROM vec").fetchone()[0]
        n_map = con.execute("SELECT COUNT(*) FROM vec_map").fetchone()[0]
    finally:
        con.close()
    assert n_vec == n_map == 1


@vec_only
def test_rebuild_after_model_change_drops_old_vectors(tmp_path, monkeypatch):
    """Swapping the embedding model changes the vector dim; a rebuild must
    drop the stale vec table instead of crashing on a dim mismatch."""
    import piia_engram.search_index as si

    db = tmp_path / "search_index.db"

    # First build: pretend model "A" with dim 8.
    monkeypatch.setattr(si, "EMBED_MODEL", "model-A")
    monkeypatch.setattr(si, "EMBED_DIM", 8)
    monkeypatch.setattr(si, "_embed", lambda texts: [[0.1] * 8 for _ in texts])
    idx = SearchIndex(db, enable_vector=True)
    idx.rebuild([{"id": "1", "summary": "alpha"}, {"id": "2", "summary": "beta"}])

    # Swap to model "B" with a different dim 16 — must not raise.
    monkeypatch.setattr(si, "EMBED_MODEL", "model-B")
    monkeypatch.setattr(si, "EMBED_DIM", 16)
    monkeypatch.setattr(si, "_embed", lambda texts: [[0.2] * 16 for _ in texts])
    idx.rebuild([{"id": "1", "summary": "alpha"}, {"id": "2", "summary": "beta"}])

    import sqlite_vec
    con = sqlite3.connect(str(db))
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        # vec recreated at new dim; map consistent; model marker updated.
        assert con.execute("SELECT COUNT(*) FROM vec").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM vec_map").fetchone()[0] == 2
        model = con.execute("SELECT value FROM meta WHERE key='embed_model'").fetchone()[0]
        assert model == "model-B"
    finally:
        con.close()


def test_cjk_segment_bigrams_and_ascii():
    """FTS segmentation: CJK runs -> overlapping bigrams; ASCII -> words."""
    from piia_engram.search_index import _cjk_segment

    assert _cjk_segment("消息队列") == ["消息", "息队", "队列"]
    assert _cjk_segment("Vite 构建") == ["vite", "构建"]
    assert _cjk_segment("a") == ["a"]
    assert _cjk_segment("好") == ["好"]


@vec_only
def test_hybrid_includes_vector_signal_with_no_lexical_overlap(tmp_path):
    idx = _vidx(tmp_path)
    idx.rebuild([
        {"id": "1", "summary": "reciprocal rank fusion merges multiple ranked lists"},
        {"id": "2", "summary": "grocery shopping list for the weekend barbecue"},
    ])
    # query is semantically about #1 but shares few/no exact tokens
    fused = idx.hybrid_search("combining several ordered result sets into one", keyword_ranking=[], limit=5)
    ids = [i for i, _ in fused]
    assert ids and ids[0] == "1"


@vec_only
def test_vector_search_empty_before_build(tmp_path):
    idx = _vidx(tmp_path)
    assert idx.vector_search("anything") == []


# ── semantic_neighbors: scored near-duplicate primitive (Round 3) ─────────


def test_semantic_neighbors_disabled_returns_empty(tmp_path):
    """No vector backend → empty, never raises."""
    idx = SearchIndex(tmp_path / "search_index.db", enable_vector=False)
    assert idx.semantic_neighbors("anything") == []


def test_semantic_neighbors_missing_db_returns_empty_no_create(tmp_path):
    """Querying neighbors must NOT create the index file (no write-path side
    effect). With the backend 'enabled' but no index on disk, return []."""
    db = tmp_path / "search_index.db"
    idx = SearchIndex(db, enable_vector=True)
    assert idx.semantic_neighbors("anything") == []
    assert not db.exists()


@vec_ext_only
def test_semantic_neighbors_cosine_known_vectors(tmp_path, monkeypatch):
    """Pin the distance→similarity contract with hand-crafted vectors:
    identical direction → 1.0, orthogonal → 0.0, best-first ordering."""
    import piia_engram.search_index as si

    monkeypatch.setattr(si, "EMBED_DIM", 4)

    def fake_embed(texts):
        out = []
        for t in texts:
            if "ALPHA" in t:
                out.append([1.0, 0.0, 0.0, 0.0])
            elif "BETA" in t:
                out.append([0.0, 1.0, 0.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0, 0.0])
        return out

    monkeypatch.setattr(si, "_embed", fake_embed)

    idx = SearchIndex(tmp_path / "search_index.db", enable_vector=True)
    idx.rebuild([
        {"id": "alpha", "summary": "ALPHA topic"},
        {"id": "beta", "summary": "BETA topic"},
    ])
    res = idx.semantic_neighbors("ALPHA query", limit=5, min_similarity=0.0)
    scored = dict(res)
    assert abs(scored["alpha"] - 1.0) < 1e-6   # cos of identical direction
    assert abs(scored["beta"] - 0.0) < 1e-6    # orthogonal
    assert res[0][0] == "alpha"                # best first


@vec_ext_only
def test_semantic_neighbors_filters_below_threshold(tmp_path, monkeypatch):
    import piia_engram.search_index as si

    monkeypatch.setattr(si, "EMBED_DIM", 4)

    def fake_embed(texts):
        out = []
        for t in texts:
            if "ALPHA" in t:
                out.append([1.0, 0.0, 0.0, 0.0])
            elif "BETA" in t:
                out.append([0.0, 1.0, 0.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0, 0.0])
        return out

    monkeypatch.setattr(si, "_embed", fake_embed)

    idx = SearchIndex(tmp_path / "search_index.db", enable_vector=True)
    idx.rebuild([
        {"id": "alpha", "summary": "ALPHA topic"},
        {"id": "beta", "summary": "BETA topic"},
    ])
    res = idx.semantic_neighbors("ALPHA query", limit=5, min_similarity=0.5)
    ids = [eid for eid, _ in res]
    assert "alpha" in ids
    assert "beta" not in ids   # cos 0.0 < 0.5 → filtered out


@vec_ext_only
def test_semantic_neighbors_empty_index_returns_empty(tmp_path, monkeypatch):
    import piia_engram.search_index as si

    monkeypatch.setattr(si, "EMBED_DIM", 4)
    monkeypatch.setattr(si, "_embed", lambda texts: [[1.0, 0.0, 0.0, 0.0] for _ in texts])
    idx = SearchIndex(tmp_path / "search_index.db", enable_vector=True)
    idx.rebuild([])   # vec table exists but holds nothing
    assert idx.semantic_neighbors("anything") == []


@vec_ext_only
def test_semantic_neighbors_no_vec_table_returns_empty(tmp_path):
    """A pre-vector (FTS-only) index on disk → no vec table → []."""
    db = tmp_path / "search_index.db"
    SearchIndex(db, enable_vector=False).rebuild([{"id": "1", "summary": "x"}])
    assert SearchIndex(db, enable_vector=True).semantic_neighbors("x") == []
