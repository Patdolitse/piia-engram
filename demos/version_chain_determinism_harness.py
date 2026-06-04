"""Version-chain & supersession determinism evidence harness (Task 2, C+).

Proves, on a *fixed synthetic corpus* of typed relation edges, that the
version-chain read layer (:mod:`piia_engram.version_chain`) is deterministic and
that superseded ids never surface as a current HEAD:

- ``build_version_report`` / ``head_ids`` / ``collapse_to_heads`` are pure and
  store-free, so the same corpus always produces byte-identical output across
  temp dirs and runs.
- A superseded id (target of a ``supersedes`` edge) is hidden by default recall
  collapse and is never reported as a HEAD of its chain.

Safety invariants:
- No store access, no temp writes required (the corpus is in-memory and fixed).
- Output is metadata-only (ids + counts) - no knowledge bodies ever appear, so
  the golden JSON is safe to diff in CI.

Run from the repo root::

    python demos/version_chain_determinism_harness.py            # human summary
    python demos/version_chain_determinism_harness.py --json      # golden JSON

The corpus is embedded here so the harness has no external inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from piia_engram import version_chain as vc  # noqa: E402

# Fixed synthetic corpus. Three independent chains exercising every shape:
#   chain A: a3 supersedes a2 supersedes a1   -> HEAD a3, superseded {a1, a2}
#   chain B: b1 led_to b2, b2 implemented_by b3 -> HEAD b3, none superseded
#   chain C: c2 supersedes c1                  -> HEAD c2, superseded {c1}
# Ids are lowercase so the lexicographic seed selection is unambiguous.
_SYNTHETIC_EDGES: list[dict[str, str]] = [
    {"src": "a2", "rel": "supersedes", "dst": "a1"},
    {"src": "a3", "rel": "supersedes", "dst": "a2"},
    {"src": "b1", "rel": "led_to", "dst": "b2"},
    {"src": "b2", "rel": "implemented_by", "dst": "b3"},
    {"src": "c2", "rel": "supersedes", "dst": "c1"},
]

# Items a default recall would have already fetched (metadata-only; no bodies).
_SYNTHETIC_ITEMS: list[dict[str, Any]] = [
    {"id": "a1", "tier": "verified"},
    {"id": "a2", "tier": "verified"},
    {"id": "a3", "tier": "verified"},
    {"id": "b1", "tier": "verified"},
    {"id": "b2", "tier": "verified"},
    {"id": "b3", "tier": "verified"},
    {"id": "c1", "tier": "verified"},
    {"id": "c2", "tier": "verified"},
]


def _corpus_fingerprint(edges: list[dict[str, str]]) -> str:
    """Stable sha256 over the canonical edge set; ties the golden to this corpus."""
    canonical = json.dumps(
        sorted(edges, key=lambda e: (e["src"], e["rel"], e["dst"])),
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()


def evaluate_invariants(
    *,
    heads: list[str],
    superseded: list[str],
    kept_ids: list[str],
    collapsed_ids: list[str],
) -> dict[str, bool]:
    """Evaluate the harness supersession invariants over metadata-only ids."""
    superseded_set = set(superseded)
    heads_set = set(heads)
    kept_set = set(kept_ids)
    return {
        "superseded_never_head": not (superseded_set & heads_set),
        "collapsed_matches_superseded": set(collapsed_ids) == superseded_set,
        "no_superseded_in_kept": not (kept_set & superseded_set),
    }


def run_harness(
    edges: list[dict[str, str]] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the deterministic version-chain evidence report."""
    edges = list(edges if edges is not None else _SYNTHETIC_EDGES)
    items = list(items if items is not None else _SYNTHETIC_ITEMS)

    report = vc.build_version_report(edges)
    heads = sorted(vc.head_ids(edges))
    from piia_engram import decision_thread as _dt

    superseded = sorted(_dt.superseded_ids(_dt.validate_edges(edges)))

    kept, collapsed = vc.collapse_to_heads(items, edges)
    kept_ids = [str(item.get("id") or "") for item in kept]
    collapsed_ids = sorted(collapsed)
    invariants = evaluate_invariants(
        heads=heads,
        superseded=superseded,
        kept_ids=kept_ids,
        collapsed_ids=collapsed_ids,
    )

    return {
        "schema": 1,
        "harness": "version_chain_determinism_v1",
        "synthetic_only": True,
        "corpus_fingerprint": _corpus_fingerprint(edges),
        "version_report": report,
        "head_ids": heads,
        "superseded_ids": superseded,
        "recall_collapse": {
            "input_ids": [str(item.get("id") or "") for item in items],
            "kept_ids": kept_ids,
            "collapsed_ids": collapsed_ids,
        },
        "invariants": invariants,
        "overall_passed": all(invariants.values()),
    }


def render_text(report: dict[str, Any]) -> str:
    inv = report.get("invariants", {})
    lines = [
        "Engram version-chain determinism harness (synthetic, store-free)",
        f"  corpus: {report.get('corpus_fingerprint', '')[:16]}…",
        f"  topics: {report['version_report']['totals']['topics']}  "
        f"heads: {report['head_ids']}  superseded: {report['superseded_ids']}",
        f"  collapse kept: {report['recall_collapse']['kept_ids']}",
        f"  collapse hid:  {report['recall_collapse']['collapsed_ids']}",
        f"  superseded_never_head={inv.get('superseded_never_head')}  "
        f"collapsed_matches_superseded={inv.get('collapsed_matches_superseded')}",
        f"  overall: {'PASS' if report.get('overall_passed') else 'FAIL'}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic version-chain determinism evidence harness."
    )
    parser.add_argument("--json", action="store_true", help="Emit golden JSON instead of text.")
    args = parser.parse_args()
    report = run_harness()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0 if report["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
