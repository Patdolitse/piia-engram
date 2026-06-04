"""Knowledge-recall ranking reproducibility benchmark (Task 3, C+).

Pins the *local* keyword ranker (:meth:`RetrievalMixin._score_item`) against a
frozen synthetic query corpus and measures hit@k and ordering stability — with
no real memories, no store, and no remote embeddings/providers.

The ranker is pure: ``_score_item`` depends only on the query terms, the item
fields, and import-time-static alias/weight tables. So the same corpus + queries
always produce byte-identical scores, which is exactly what makes a regression
in ranking quality detectable: a degraded ranker (or a corpus where the expected
hit is missing) drops hit@k below the frozen baseline.

Safety invariants:
- No store, no temp dir, no network, no embedding model — a bare mixin instance
  scores in-memory synthetic dicts.
- Output is metadata-only (ids, ranks, rounded scores) and byte-stable.

Run from the repo root::

    python demos/recall_ranking_benchmark.py            # human summary
    python demos/recall_ranking_benchmark.py --json      # golden JSON
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram.retrieval import RetrievalMixin  # noqa: E402


class _Scorer(RetrievalMixin):
    """Bare holder for the pure ``_score_item`` / tokenizer methods (no store)."""


_SCORER = _Scorer()

# Frozen synthetic corpus. Fake knowledge only — English keywords keep the
# tokenizer behavior unambiguous. access_count omitted so the log1p term is 0.
_CORPUS: list[dict[str, Any]] = [
    {"id": "K1", "summary": "python packaging and wheel build best practices", "domain": "python"},
    {"id": "K2", "summary": "javascript frontend react rendering performance", "domain": "frontend"},
    {"id": "K3", "summary": "mcp server tool registration and json schema", "domain": "mcp_dev"},
    {"id": "K4", "summary": "release pipeline auth and pypi publishing flow", "domain": "release"},
    {"id": "K5", "summary": "git rebase workflow and merge conflict resolution", "domain": "git"},
    {"id": "K6", "question": "which database for the local knowledge store", "choice": "sqlite", "domain": "storage"},
    {"id": "K7", "summary": "python async event loop and concurrency patterns", "domain": "python"},
    {"id": "K8", "summary": "backup restore and migration round trip safety", "domain": "ops"},
]

# Frozen queries with the id each is expected to rank first.
_QUERIES: list[dict[str, Any]] = [
    {"query": "python packaging wheel", "expected": "K1"},
    {"query": "react frontend rendering performance", "expected": "K2"},
    {"query": "mcp tool json schema", "expected": "K3"},
    {"query": "pypi release auth pipeline", "expected": "K4"},
    {"query": "git rebase merge conflict", "expected": "K5"},
    {"query": "sqlite database local store", "expected": "K6"},
    {"query": "python async concurrency", "expected": "K7"},
    {"query": "backup restore migration", "expected": "K8"},
]

_TOP_K = 3


def _default_scorer(item: dict[str, Any], terms: list[str]) -> float:
    return _SCORER._score_item(item, terms)


def _rank_query(
    corpus: list[dict[str, Any]],
    query: str,
    *,
    scorer: Callable[[dict[str, Any], list[str]], float],
    top_k: int,
) -> list[tuple[str, float]]:
    """Score every corpus item and return the top-k ``(id, score)`` pairs.

    Ties break deterministically by id so ordering is reproducible regardless of
    input order.
    """
    terms = [t for t in query.lower().split() if t]
    scored = [
        (str(item.get("id") or ""), round(float(scorer(item, terms)), 4))
        for item in corpus
    ]
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:top_k]


def _corpus_fingerprint(corpus: list[dict[str, Any]], queries: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {"corpus": corpus, "queries": queries},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()


def run_benchmark(
    corpus: list[dict[str, Any]] | None = None,
    queries: list[dict[str, Any]] | None = None,
    *,
    scorer: Callable[[dict[str, Any], list[str]], float] | None = None,
    top_k: int = _TOP_K,
) -> dict[str, Any]:
    """Run the frozen ranking benchmark and return a deterministic report."""
    corpus = list(corpus if corpus is not None else _CORPUS)
    queries = list(queries if queries is not None else _QUERIES)
    scorer = scorer or _default_scorer

    rows: list[dict[str, Any]] = []
    hit_at_1 = 0
    hit_at_k = 0
    reciprocal_ranks: list[float] = []
    for q in queries:
        query = str(q.get("query") or "")
        expected = str(q.get("expected") or "")
        ranked = _rank_query(corpus, query, scorer=scorer, top_k=top_k)
        ranked_ids = [eid for eid, _ in ranked]
        rank_of_expected = (
            ranked_ids.index(expected) + 1 if expected in ranked_ids else 0
        )
        is_hit_1 = bool(ranked_ids and ranked_ids[0] == expected)
        is_hit_k = expected in ranked_ids
        hit_at_1 += int(is_hit_1)
        hit_at_k += int(is_hit_k)
        reciprocal_ranks.append(1.0 / rank_of_expected if rank_of_expected else 0.0)
        rows.append({
            "query": query,
            "expected": expected,
            "ranked": ranked,
            "rank_of_expected": rank_of_expected,
            "hit_at_1": is_hit_1,
            "hit_at_k": is_hit_k,
        })

    n = len(queries) or 1
    mrr = round(sum(reciprocal_ranks) / n, 4)
    return {
        "schema": 1,
        "harness": "recall_ranking_benchmark_v1",
        "synthetic_only": True,
        "remote_embeddings": False,
        "store_access": False,
        "top_k": top_k,
        "corpus_fingerprint": _corpus_fingerprint(corpus, queries),
        "query_count": len(queries),
        "hit_at_1": hit_at_1,
        "hit_at_k": hit_at_k,
        "mrr": mrr,
        "rows": rows,
        "overall_passed": hit_at_1 == len(queries),
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Engram recall-ranking reproducibility benchmark (synthetic, no store)",
        f"  corpus: {report.get('corpus_fingerprint', '')[:16]}…",
        f"  queries: {report['query_count']}  hit@1: {report['hit_at_1']}  "
        f"hit@{report['top_k']}: {report['hit_at_k']}  mrr: {report['mrr']}",
    ]
    for row in report["rows"]:
        mark = "ok" if row["hit_at_1"] else "!!"
        lines.append(
            f"  [{mark}] {row['query']!r} -> {row['ranked'][0][0] if row['ranked'] else '-'} "
            f"(expected {row['expected']}, rank {row['rank_of_expected']})"
        )
    lines.append(f"  overall: {'PASS' if report['overall_passed'] else 'FAIL'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic recall-ranking reproducibility benchmark."
    )
    parser.add_argument("--json", action="store_true", help="Emit golden JSON instead of text.")
    args = parser.parse_args()
    report = run_benchmark()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
