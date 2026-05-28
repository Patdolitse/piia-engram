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

import re
import sqlite3
from pathlib import Path
from typing import Iterable

# Standard RRF constant (decision a1309293caf5). Larger k => flatter
# contribution from rank position; 60 is the value from the original RRF
# paper and what every reference implementation defaults to.
RRF_K = 60

# FastEmbed model id for the embedding layer (next increment).
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBED_DIM = 384

# Fields concatenated into the FTS document for each entry, in priority
# order. Mirrors the primary-text logic in retrieval.py so both signals
# index the same surface.
_TEXT_FIELDS = ("summary", "title", "question", "choice", "content", "reasoning", "domain")

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")


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
    tokens = _WORD_RE.findall(query.lower())
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
        return sqlite3.connect(str(self.db_path))

    def rebuild(self, entries: list[dict]) -> int:
        """(Re)build the FTS index from ``entries``.

        Each entry must carry an ``id``; entries without one are skipped.
        Returns the number of documents indexed. Idempotent: safe to call
        repeatedly (drops and recreates the table each time).
        """
        con = self._connect()
        try:
            con.execute("DROP TABLE IF EXISTS fts")
            con.execute(
                "CREATE VIRTUAL TABLE fts USING fts5("
                "eid UNINDEXED, doc, tokenize='unicode61')"
            )
            rows = [
                (str(e["id"]), _entry_document(e))
                for e in entries
                if e.get("id")
            ]
            con.executemany("INSERT INTO fts(eid, doc) VALUES (?, ?)", rows)
            con.commit()
            return len(rows)
        finally:
            con.close()

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

        Next-increment hook: when the ``[vector]`` extra is installed this
        embeds the query with FastEmbed and runs a sqlite-vec KNN search.
        Until that increment lands (and can be verified against the real
        deps), this returns an empty ranking so the fusion simply runs on
        keyword + FTS — never raising on a vector-less install.
        """
        if not self.vector_enabled:
            return []
        # TODO(v4.0 next increment): embed via FastEmbed(EMBED_MODEL),
        # KNN over a sqlite-vec vec0(float[EMBED_DIM]) table keyed to eid.
        return []

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
