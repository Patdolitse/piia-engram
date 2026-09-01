"""v4.0 hybrid search — rebuildable SQLite index over the JSON store.

Recorded architecture (decisions d49043029e53 + a1309293caf5):

- **JSON stays the single source of truth.** This SQLite file
  (``search_index.db``) is a *rebuildable* index: deleting it loses
  nothing; it's recreated from the JSON knowledge files. That keeps the
  local-first guarantee (the user's memory is plain, editable JSON) while
  adding a fast retrieval layer on top.

- **Three retrieval signals, fused with Reciprocal Rank Fusion (k=60):**
    1. ``keyword`` — the existing token-overlap scorer in retrieval.py
       (handles CJK n-grams + alias expansion).
    2. ``fts`` — full-text via FTS5 ``unicode61`` (ships with sqlite,
       zero extra deps).
    3. ``vector`` — sqlite-vec + FastEmbed (all-MiniLM-L6-v2), OPTIONAL
       (the ``[vector]`` extra). Lands in the next v4.0 increment; this
       module already probes for it and leaves the RRF call ready to take
       a third ranking.

RRF is rank-based, so it fuses signals on different score scales with no
``alpha`` to tune — only the standard constant ``k=60``.

Scope of THIS increment: the zero-dep core (RRF + FTS5 index) that can be
fully tested without optional deps. It is intentionally standalone and
NOT yet wired into ``search_knowledge`` — the cutover is a later step so
the stable keyword path is untouched while this is validated.

Known limitation (documented, not hidden): ``unicode61`` does not segment
CJK text — a run of Chinese characters becomes one token. The existing
keyword scorer already covers CJK via n-grams, and RRF fusion bridges the
gap; proper CJK segmentation for the FTS layer is a later refinement.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable

# Standard RRF constant (decision a1309293caf5). Larger k => flatter
# contribution from rank position; 60 is the value from the original RRF
# paper and what every reference implementation defaults to.
RRF_K = 60

# FastEmbed model id for the semantic layer. Default is a Chinese-first
# small model (this store is CJK-heavy); override with ENGRAM_EMBED_MODEL.
# Dim must match the model — kept in a small map so we don't import
# fastembed just to read it at module load.
_MODEL_DIMS = {
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-small-en-v1.5": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    "intfloat/multilingual-e5-large": 1024,
}
EMBED_MODEL = os.environ.get("ENGRAM_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
EMBED_DIM = _MODEL_DIMS.get(EMBED_MODEL, 512)


def validated_embed_dim() -> int | None:
    """v4.20 SQL discipline (fail closed): the vector DDL interpolates the
    embedding dimension, so the dimension is only valid when EMBED_MODEL is
    in the CLOSED mapping above and its dimension is a positive int. An
    unknown model returns None — callers must skip vector-DDL work entirely
    (no silent 512 fallback, which would build SQL from an unvalidated value).
    Re-checked at every DDL execution, not just import time (reload/patching
    of EMBED_MODEL cannot smuggle a value through)."""
    model = os.environ.get("ENGRAM_EMBED_MODEL", EMBED_MODEL)
    dim = _MODEL_DIMS.get(model)
    if isinstance(dim, int) and not isinstance(dim, bool) and dim > 0:
        return dim
    return None


# v4.20.1: the ONE audited DDL source is a CLOSED-SET LITERAL TABLE keyed by
# the validated dimension — no interpolation of any kind builds vec DDL. The
# keys mirror _MODEL_DIMS (the closed set validated_embed_dim enforces); a dim
# missing from the table is a programmer error (KeyError), never SQL built
# from an unvalidated value.
_VEC_DDL_BY_DIM = {
    384: ("CREATE VIRTUAL TABLE IF NOT EXISTS vec "
          "USING vec0(embedding float[384])"),
    512: ("CREATE VIRTUAL TABLE IF NOT EXISTS vec "
          "USING vec0(embedding float[512])"),
    1024: ("CREATE VIRTUAL TABLE IF NOT EXISTS vec "
           "USING vec0(embedding float[1024])"),
}


def _vec_ddl(dim: int) -> str:
    return _VEC_DDL_BY_DIM[int(dim)]

# Fields concatenated into the FTS document for each entry, in priority
# order. Mirrors the primary-text logic in retrieval.py so both signals
# index the same surface.
_TEXT_FIELDS = ("summary", "title", "question", "choice", "content", "reasoning", "domain")

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")

# Runs of ASCII word chars OR runs of CJK ideographs.
_SEG_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]+")


def _cjk_segment(text: str) -> list[str]:
    """Tokenize text the way the keyword scorer does, so the FTS layer
    stops being CJK-blind.

    FTS5 ``unicode61`` treats a whole run of Chinese as ONE token, so
    ``"消息队列"`` only matches the identical 4-gram. We instead emit
    overlapping CJK *bigrams* (plus the single char for length-1 runs) and
    lowercased ASCII words — matching retrieval.py's n-gram approach. Used
    for BOTH the indexed document and the query, so they share a
    vocabulary.
    """
    out: list[str] = []
    for m in _SEG_RE.finditer(text):
        tok = m.group(0)
        if tok[0].isascii():
            out.append(tok.lower())
        elif len(tok) == 1:
            out.append(tok)
        else:
            out.extend(tok[i:i + 2] for i in range(len(tok) - 1))
    return out


def _fts_document(text: str) -> str:
    """Space-joined segmented form of a document, for FTS5 indexing."""
    return " ".join(_cjk_segment(text))


def reciprocal_rank_fusion(
    rankings: Iterable[Iterable[str]],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Fuse several ranked id-lists into one ranking via RRF.

    Args:
        rankings: an iterable of ranked id-lists, each best-first. Each
            list is one retrieval signal (keyword / fts / vector). Missing
            ids in a list simply contribute nothing from that signal.
        k: RRF constant (default 60).

    Returns:
        ``[(id, score), ...]`` sorted by score desc, then id asc for a
        stable, deterministic order. An item's score is
        ``sum(1 / (k + rank))`` over every list it appears in (rank is
        1-based).
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def vector_backend_available() -> bool:
    """True if the optional ``[vector]`` deps are importable.

    Probed (not cached) so a freshly installed extra is picked up without
    re-import gymnastics. Callers use this to decide whether to add a
    vector ranking to the RRF fusion.
    """
    try:
        import fastembed  # noqa: F401
        import sqlite_vec  # noqa: F401
    except Exception:
        return False
    return True


_MODEL = None


def _embedding_model():
    """Lazily build the FastEmbed model (singleton).

    Cold init is the expensive part (loads the ONNX model), so we build it
    once per process. Honors a caller-set cache dir via ``FASTEMBED_CACHE_PATH``
    so model files land where the host wants them (kept off the system drive
    on this setup) rather than the default HF cache.
    """
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding

        cache_dir = os.environ.get("FASTEMBED_CACHE_PATH") or None
        _MODEL = TextEmbedding(model_name=EMBED_MODEL, cache_dir=cache_dir)
    return _MODEL


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts into ``EMBED_DIM``-length float vectors."""
    return [list(map(float, v)) for v in _embedding_model().embed(texts)]


def _content_hash(text: str) -> str:
    """Short stable fingerprint of an entry's document, for incremental
    re-embedding (only re-embed when the text actually changed)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _entry_document(entry: dict) -> str:
    """Build the full-text document string for one knowledge entry."""
    parts: list[str] = []
    for field in _TEXT_FIELDS:
        raw = entry.get(field)
        if not raw:
            continue
        if isinstance(raw, (list, tuple)):
            parts.append(" ".join(str(v) for v in raw))
        else:
            parts.append(str(raw))
    return "\n".join(parts)


def _fts_match_expr(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Extracts word/CJK tokens and OR-joins them as quoted terms, so
    punctuation in the raw query can't produce an FTS5 syntax error.
    Returns "" when the query has no usable tokens.
    """
    tokens = _cjk_segment(query)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


class SearchIndex:
    """Rebuildable FTS5 index over knowledge entries.

    Lifecycle: build/rebuild it from the JSON entries, then query. The
    backing file is disposable — ``rebuild`` drops and recreates the
    table, so a corrupt or stale index is fixed by rebuilding (or by
    deleting the file and rebuilding).
    """

    def __init__(self, db_path: str | Path, *, enable_vector: bool | None = None):
        self.db_path = Path(db_path)
        # None => auto-probe; explicit bool => caller override (tests).
        self.vector_enabled = (
            vector_backend_available() if enable_vector is None else enable_vector
        )

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_path))
        if self.vector_enabled:
            try:
                import sqlite_vec

                con.enable_load_extension(True)
                sqlite_vec.load(con)
                con.enable_load_extension(False)
            except Exception:
                # extension missing/unloadable at runtime — degrade to FTS.
                self.vector_enabled = False
        return con

    def rebuild(self, entries: list[dict], fingerprint: str | None = None) -> int:
        """(Re)build the index from ``entries``.

        FTS is rebuilt wholesale (cheap). The vector table, when enabled, is
        updated *incrementally*: only new or content-changed entries are
        re-embedded, and removed entries are dropped — so a rebuild after a
        single new lesson doesn't re-embed the whole store.

        ``fingerprint`` (when given) is stored so the caller can later detect
        whether the source JSON changed since this build (freshness check).

        Each entry must carry an ``id``; entries without one are skipped.
        Returns the number of documents indexed (FTS row count). Idempotent.
        """
        con = self._connect()
        try:
            docs = [
                (str(e["id"]), _entry_document(e))
                for e in entries
                if e.get("id")
            ]
            con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
            # --- FTS: full rebuild ---
            con.execute("DROP TABLE IF EXISTS fts")
            con.execute(
                "CREATE VIRTUAL TABLE fts USING fts5("
                "eid UNINDEXED, doc, tokenize='unicode61')"
            )
            # Index the CJK-segmented form so Chinese is matchable; the raw
            # `docs` text is kept for embeddings + content hashing.
            con.executemany(
                "INSERT INTO fts(eid, doc) VALUES (?, ?)",
                [(eid, _fts_document(doc)) for eid, doc in docs],
            )

            # --- vector: incremental ---
            if self.vector_enabled:
                self._rebuild_vectors(con, docs)

            # --- markers: embed model (for dim-change detection) + freshness ---
            # Only record the model when the vector layer is actually in use,
            # so the marker can't misrepresent a non-vector index.
            if self.vector_enabled:
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('embed_model', ?)",
                    (EMBED_MODEL,),
                )
            if fingerprint is not None:
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES ('fingerprint', ?)",
                    (fingerprint,),
                )
            con.commit()
            return len(docs)
        finally:
            con.close()

    def fingerprint(self) -> str | None:
        """Return the stored freshness marker, or None if never built."""
        con = self._connect()
        try:
            try:
                row = con.execute(
                    "SELECT value FROM meta WHERE key = 'fingerprint'"
                ).fetchone()
            except sqlite3.OperationalError:
                return None
            return row[0] if row else None
        finally:
            con.close()

    def has_vector_table(self) -> bool:
        """True if the vec0 vector table exists in the index.

        Lets the caller detect a stale FTS-only index that predates the
        vector backend being installed, and force a rebuild to populate it.
        """
        con = self._connect()
        try:
            row = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='vec'"
            ).fetchone()
            return row is not None
        except sqlite3.OperationalError:
            return False
        finally:
            con.close()

    def _rebuild_vectors(self, con: sqlite3.Connection, docs: list[tuple[str, str]]) -> None:
        """Incrementally sync the vec0 table to ``docs`` (id, document)."""
        import sqlite_vec

        # If the embedding model changed since the last build, the stored
        # vectors have a different dim/semantics — drop them so they're
        # rebuilt at the current EMBED_DIM (prevents a dim-mismatch crash).
        try:
            row = con.execute(
                "SELECT value FROM meta WHERE key = 'embed_model'"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row and row[0] != EMBED_MODEL:
            con.execute("DROP TABLE IF EXISTS vec")
            con.execute("DROP TABLE IF EXISTS vec_map")

        # v4.20.1: the ONLY vec-DDL execution — closed-set validation at every
        # run; the DDL string comes solely from the audited literal table via
        # _vec_ddl (v4.20.0 shipped this site unwired with a raw fallback).
        _dim = validated_embed_dim()
        if _dim is None:
            return  # fail closed: vector layer disabled for this configuration
        con.execute(_vec_ddl(_dim))
        con.execute(
            "CREATE TABLE IF NOT EXISTS vec_map("
            "rowid INTEGER PRIMARY KEY, eid TEXT UNIQUE, chash TEXT)"
        )
        existing = {
            eid: (rowid, chash)
            for rowid, eid, chash in con.execute("SELECT rowid, eid, chash FROM vec_map")
        }
        wanted = {eid: _content_hash(doc) for eid, doc in docs}
        doc_by_eid = dict(docs)

        # Deletions: in index but no longer present.
        for eid in set(existing) - set(wanted):
            rowid = existing[eid][0]
            con.execute("DELETE FROM vec WHERE rowid = ?", (rowid,))
            con.execute("DELETE FROM vec_map WHERE rowid = ?", (rowid,))

        # New or changed entries → re-embed.
        to_embed = [
            eid for eid, chash in wanted.items()
            if eid not in existing or existing[eid][1] != chash
        ]
        if not to_embed:
            return
        vectors = _embed([doc_by_eid[eid] for eid in to_embed])
        next_rowid = (con.execute("SELECT COALESCE(MAX(rowid), 0) FROM vec_map").fetchone()[0]) + 1
        for eid, vec in zip(to_embed, vectors):
            if eid in existing:  # changed → reuse rowid, replace vector
                rowid = existing[eid][0]
                con.execute("DELETE FROM vec WHERE rowid = ?", (rowid,))
            else:                # new → allocate rowid
                rowid = next_rowid
                next_rowid += 1
            con.execute(
                "INSERT INTO vec(rowid, embedding) VALUES (?, ?)",
                (rowid, sqlite_vec.serialize_float32(vec)),
            )
            con.execute(
                "INSERT OR REPLACE INTO vec_map(rowid, eid, chash) VALUES (?, ?, ?)",
                (rowid, eid, wanted[eid]),
            )

    def fts_search(self, query: str, limit: int = 50) -> list[str]:
        """Return entry ids ranked by FTS5 bm25 relevance (best first)."""
        expr = _fts_match_expr(query)
        if not expr:
            return []
        con = self._connect()
        try:
            try:
                cur = con.execute(
                    "SELECT eid FROM fts WHERE fts MATCH ? ORDER BY rank LIMIT ?",
                    (expr, max(0, int(limit))),
                )
            except sqlite3.OperationalError:
                # No table yet (never built) — empty result, not an error.
                return []
            return [row[0] for row in cur.fetchall()]
        finally:
            con.close()

    def vector_search(self, query: str, limit: int = 50) -> list[str]:
        """Return entry ids ranked by semantic similarity (best first).

        Embeds the query with FastEmbed and runs a sqlite-vec KNN search
        over the vec0 table. Returns [] when the vector backend is absent
        (install-less setups) or the index has no vector table yet, so the
        caller's fusion simply runs on keyword + FTS.
        """
        if not self.vector_enabled:
            return []
        if not query.strip():
            return []
        try:
            import sqlite_vec

            qv = sqlite_vec.serialize_float32(_embed([query])[0])
        except Exception:
            return []
        con = self._connect()
        try:
            try:
                # KNN must run on the vec0 table alone — a JOIN hides the
                # LIMIT from sqlite-vec's knn planner ("a LIMIT or k=? is
                # required"). So fetch rowids first, then map to eids.
                rowids = [
                    r[0] for r in con.execute(
                        "SELECT rowid FROM vec WHERE embedding MATCH ? "
                        "ORDER BY distance LIMIT ?",
                        (qv, max(1, int(limit))),
                    ).fetchall()
                ]
            except sqlite3.OperationalError:
                return []  # no vec table built yet
            if not rowids:
                return []
            # v4.20.1: per-rowid parameterized lookups (rowid lists here are
            # small, <= the search limit) — no dynamically shaped SQL at all.
            eid_by_rowid = {}
            for rowid in rowids:
                hit = con.execute(
                    "SELECT eid FROM vec_map WHERE rowid = ?", (rowid,)
                ).fetchone()
                if hit and hit[0] is not None:
                    eid_by_rowid[rowid] = hit[0]
            return [eid_by_rowid[r] for r in rowids if r in eid_by_rowid]
        finally:
            con.close()

    def semantic_neighbors(
        self,
        text: str,
        limit: int = 5,
        min_similarity: float = 0.0,
    ) -> list[tuple[str, float]]:
        """Return ``(eid, cosine_similarity)`` neighbors of ``text``, best first.

        Pure additive primitive for non-destructive semantic near-duplicate
        surfacing on write (Round-3). Unlike :meth:`vector_search` (eids only),
        this exposes a calibrated score so the write path can threshold it.

        Similarity is computed via sqlite-vec's ``vec_distance_cosine`` over a
        full scan of the vec0 table (``similarity = 1 - cosine_distance``). This
        is deliberately metric-agnostic: it does NOT depend on the vec0 table's
        KNN distance metric (default L2) and is scale-invariant, so it is safe
        even though :func:`_embed` does not normalise embeddings. The corpus is
        capped at a few hundred active rows, so the full scan is trivial.

        Returns ``[]`` on ANY unavailability — vector backend absent, the index
        db does not exist yet (a read-only probe must never *create* it), no vec
        table, empty index, or an embed error — so the caller's lexical-only
        path runs unchanged.
        """
        if not self.vector_enabled:
            return []
        if not text or not text.strip():
            return []
        # Gap-2 guard: never materialise search_index.db from a neighbor probe.
        # _connect() would mkdir+create the file; bail out before that when the
        # index has not been built (keyword-only / encrypted / fresh root).
        if not self.db_path.exists():
            return []
        try:
            import sqlite_vec

            qv = sqlite_vec.serialize_float32(_embed([text])[0])
        except Exception:
            return []
        limit = max(1, int(limit))
        con = self._connect()
        try:
            try:
                rows = con.execute(
                    "SELECT m.eid, vec_distance_cosine(v.embedding, ?) AS cdist "
                    "FROM vec v JOIN vec_map m ON m.rowid = v.rowid "
                    "ORDER BY cdist ASC LIMIT ?",
                    (qv, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                return []  # no vec table built yet
            out: list[tuple[str, float]] = []
            for eid, cdist in rows:
                if eid is None or cdist is None:
                    continue
                sim = 1.0 - float(cdist)
                if sim < 0.0:
                    sim = 0.0
                elif sim > 1.0:
                    sim = 1.0
                if sim >= min_similarity:
                    out.append((eid, sim))
            return out
        finally:
            con.close()

    def hybrid_search(
        self,
        query: str,
        keyword_ranking: list[str],
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Fuse the caller's keyword ranking with FTS (and vector, when
        available) via RRF and return the top ``limit`` ``(id, score)``.

        ``keyword_ranking`` is the id list from the existing token scorer,
        passed in so this module stays decoupled from retrieval.py.
        """
        rankings = [keyword_ranking, self.fts_search(query, limit=max(limit, 50))]
        vec = self.vector_search(query, limit=max(limit, 50))
        if vec:
            rankings.append(vec)
        fused = reciprocal_rank_fusion(rankings)
        return fused[: max(0, int(limit))]
