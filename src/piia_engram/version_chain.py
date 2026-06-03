"""Version-chain read/report scaffold (Phase 6).

A *pure*, store-free read layer over the typed knowledge edges already produced
by :mod:`decision_thread` (``led_to`` / ``implemented_by`` / ``supersedes``).
Where ``decision_thread`` reconstructs a *single* thread around a seed, this
module answers the version-chain questions the recall surface needs:

- ``resolve_heads``   — for one topic, which ids are the *current* version(s)
  (active and not superseded by anything newer)?
- ``collapse_to_heads`` — given a list of already-fetched knowledge items,
  drop the ones a newer version supersedes, so default recall prefers HEAD
  (knowledge-version-chain-design.md §"Recall Resolution").
- ``lineage``        — the full ordered evolution of the topic containing a
  seed (thin wrapper over ``decision_thread.build_thread``).
- ``build_version_report`` — a metadata-only report over the *whole* edge set:
  one row per topic (connected component) with heads / superseded counts /
  cycle flag. No content, no store access.

Design constraints (mirrors ``recall.py`` / ``decision_thread.py``):
- stdlib only, side-effect free, safe to unit-test with fixtures.
- never reads the store; callers pass already-loaded ``edges`` + ``entries``.
- never mutates inputs; returns fresh dicts/lists.
- this is a *read/report* scaffold: it proposes nothing destructive and writes
  nothing. The write-path version fields (parent_id/root_id/derives_from) remain
  deferred per the design spec; this layer works with what the store has today
  (``supersedes`` edges) and degrades gracefully when richer fields arrive.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import decision_thread as _dt


def resolve_heads(seed_id: str, edges: Iterable[dict]) -> list[str]:
    """Return the current head id(s) of the topic containing ``seed_id``.

    A *head* is an active (non-superseded) node with no outgoing forward edge —
    i.e. the latest version along the evolution. Empty if the seed has no edges.
    """
    thread = _dt.build_thread(seed_id, edges)
    return list(thread.get("heads", []))


def _superseded_set(edges: list[dict]) -> set[str]:
    """All ids that are the target of *some* supersedes edge (globally obsolete)."""
    return _dt.superseded_ids(edges, scope=None)


def collapse_to_heads(
    items: Iterable[dict[str, Any]],
    edges: Iterable[dict],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Filter ``items`` to drop any whose id a newer version supersedes.

    Returns ``(kept, collapsed_ids)`` where ``kept`` preserves the input order
    and contains every item that is **not** globally superseded (plus every item
    that has no id, which can't participate in a chain). ``collapsed_ids`` lists
    the ids that were dropped, so a caller can surface "N older versions hidden".

    Pure and non-destructive: items are returned by reference in their original
    order; nothing is mutated. This is the default-recall "prefer HEAD" behavior;
    full lineage stays available via :func:`lineage`.
    """
    valid = _dt.validate_edges(edges)
    superseded = _superseded_set(valid)
    kept: list[dict[str, Any]] = []
    collapsed: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue  # malformed — ignore, never crash (mirrors decision_thread)
        eid = item.get("id")
        if isinstance(eid, str) and eid in superseded:
            collapsed.append(eid)
            continue
        kept.append(item)
    return kept, collapsed


def head_ids(edges: Iterable[dict]) -> set[str]:
    """Return the set of *current HEAD* ids across every version chain.

    A HEAD is the latest version of a topic (active, no newer version supersedes
    it, no outgoing forward edge). Pure and store-free: derived from the same
    per-topic reconstruction as :func:`build_version_report`. Used for render-only
    "this is the current version" surfacing in recall / resume / dashboard.
    """
    report = build_version_report(edges)
    heads: set[str] = set()
    for topic in report.get("topics", []):
        heads.update(topic.get("heads", []))
    return heads


def lineage(
    seed_id: str,
    edges: Iterable[dict],
    entries: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Full ordered lineage of the topic containing ``seed_id``.

    Thin, explicit wrapper over :func:`decision_thread.build_thread` so version
    callers don't depend on the thread module directly and so the "history walk"
    entry point reads clearly at the call site.
    """
    return _dt.build_thread(seed_id, edges, entries)


def build_version_report(
    edges: Iterable[dict],
    entries: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Metadata-only report over the whole edge set, grouped by topic.

    Returns::

        {
          "topics": [
            {"seed": "<smallest id in the component>",
             "size": <#nodes>,
             "heads": [id, ...],
             "active_count": int,
             "superseded_count": int,
             "has_cycle": bool},
            ...
          ],
          "totals": {"topics": int, "nodes": int, "heads": int,
                     "superseded": int, "cycles": int},
        }

    Topics are deterministic (one row per connected component, keyed by the
    lexicographically smallest node id; rows sorted by that seed) so the report
    is stable across runs. Content never appears — only ids and counts.
    """
    valid = _dt.validate_edges(edges)

    # Collect every node that participates in at least one edge.
    nodes: set[str] = set()
    for e in valid:
        nodes.add(e["src"])
        nodes.add(e["dst"])

    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in sorted(nodes):
        if node in seen:
            continue
        component = _dt.connected_component(node, valid)
        if not component:
            continue
        seen |= component
        seed = min(component)
        thread = _dt.build_thread(seed, valid, entries)
        superseded = _dt.superseded_ids(valid, component)
        topics.append({
            "seed": seed,
            "size": len(component),
            "heads": list(thread.get("heads", [])),
            "active_count": len(thread.get("active_ids", [])),
            "superseded_count": len(superseded),
            "has_cycle": bool(thread.get("has_cycle", False)),
        })

    topics.sort(key=lambda t: t["seed"])
    totals = {
        "topics": len(topics),
        "nodes": len(nodes),
        "heads": sum(len(t["heads"]) for t in topics),
        "superseded": sum(t["superseded_count"] for t in topics),
        "cycles": sum(1 for t in topics if t["has_cycle"]),
    }
    return {"topics": topics, "totals": totals}
