"""c0 — Decision Threads (scaffold).

The differentiation layer (design doc §1.c): reconstruct *how a decision
evolved* — idea → ... → decision → implementation → outcome — across
sessions and tools. This is what makes Engram "understand the user" rather
than merely "store" them (信条一): the thread is the visible product of that
understanding, and the demoable hook (`get_decision_thread`).

Relations are **typed and directed** (unlike core's untyped, symmetric
``related_ids`` "see also" links):

- ``led_to``        : A led_to B   — A came before / caused B (forward)
- ``implemented_by``: D implemented_by I — decision D was realized by I (forward)
- ``supersedes``    : N supersedes O — N replaces O; O becomes obsolete

Scope of THIS scaffold: pure graph logic over a supplied set of edges +
entries — fully testable in isolation. Where typed edges are stored in
``~/.engram`` and the MCP/CLI surface (`get_decision_thread`) are the next
increment; the live store is untouched here.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Iterable

RELATION_TYPES = ("led_to", "supersedes", "implemented_by")
# Edges that define the temporal/causal forward direction (src → dst).
_FORWARD = frozenset({"led_to", "implemented_by"})


def validate_edges(edges: Iterable[dict]) -> list[dict]:
    """Normalize + keep only well-formed edges {src, rel, dst}.

    Drops edges with an unknown relation type or missing endpoints (fail
    safe: a malformed edge is ignored, never crashes a thread build)."""
    out: list[dict] = []
    for e in edges:
        rel = str(e.get("rel", "")).strip()
        src = e.get("src")
        dst = e.get("dst")
        if rel in RELATION_TYPES and src and dst and src != dst:
            out.append({"src": str(src), "rel": rel, "dst": str(dst)})
    return out


def _undirected_adj(edges: list[dict]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adj[e["src"]].add(e["dst"])
        adj[e["dst"]].add(e["src"])
    return adj


def connected_component(seed_id: str, edges: list[dict]) -> set[str]:
    """All node ids reachable from ``seed_id`` ignoring edge direction.

    This gathers the topic's nodes; returns empty if the seed has no edges."""
    seed_id = str(seed_id)
    adj = _undirected_adj(edges)
    if seed_id not in adj:
        return set()
    seen = {seed_id}
    q = deque([seed_id])
    while q:
        n = q.popleft()
        for m in adj[n]:
            if m not in seen:
                seen.add(m)
                q.append(m)
    return seen


def superseded_ids(edges: list[dict], scope: set[str] | None = None) -> set[str]:
    """Ids that are the *target* of a supersedes edge (i.e. obsolete)."""
    out = set()
    for e in edges:
        if e["rel"] == "supersedes":
            if scope is None or (e["src"] in scope and e["dst"] in scope):
                out.add(e["dst"])
    return out


def order_thread(node_ids: set[str], edges: list[dict]) -> tuple[list[str], bool]:
    """Topologically order ``node_ids`` along forward edges (led_to /
    implemented_by). Deterministic (ties broken by id). Returns
    ``(ordered, has_cycle)``; on a cycle, the remaining nodes are appended in
    sorted order and ``has_cycle=True`` (never loops forever)."""
    nodes = set(map(str, node_ids))
    fwd: dict[str, set[str]] = defaultdict(set)
    indeg: dict[str, int] = {n: 0 for n in nodes}
    for e in edges:
        if e["rel"] in _FORWARD and e["src"] in nodes and e["dst"] in nodes:
            if e["dst"] not in fwd[e["src"]]:
                fwd[e["src"]].add(e["dst"])
                indeg[e["dst"]] += 1

    ready = deque(sorted(n for n in nodes if indeg[n] == 0))
    ordered: list[str] = []
    while ready:
        n = ready.popleft()
        ordered.append(n)
        for m in sorted(fwd[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        # keep the ready queue sorted for determinism
        ready = deque(sorted(ready))

    has_cycle = len(ordered) < len(nodes)
    if has_cycle:
        ordered.extend(sorted(nodes - set(ordered)))
    return ordered, has_cycle


def _summary(entries: dict[str, dict] | None, node_id: str) -> str:
    if not entries:
        return ""
    e = entries.get(node_id) or {}
    return str(e.get("summary") or e.get("title") or e.get("question") or "")


def build_thread(
    seed_id: str,
    edges: Iterable[dict],
    entries: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Reconstruct the decision thread containing ``seed_id``.

    Returns:
        {
          "seed": id,
          "found": bool,                # False if seed has no relations
          "has_cycle": bool,
          "order": [ {id, status, summary?}, ... ],   # evolution order
          "current": [id, ...],         # active (non-superseded) head(s)
        }
    """
    seed_id = str(seed_id)
    edges = validate_edges(edges)
    comp = connected_component(seed_id, edges)
    if not comp:
        return {"seed": seed_id, "found": False, "has_cycle": False,
                "order": [], "current": []}

    ordered, has_cycle = order_thread(comp, edges)
    sup = superseded_ids(edges, comp)
    order = []
    for n in ordered:
        row = {"id": n, "status": "superseded" if n in sup else "active"}
        if entries:
            row["summary"] = _summary(entries, n)
        order.append(row)
    current = [n for n in ordered if n not in sup]
    return {
        "seed": seed_id,
        "found": True,
        "has_cycle": has_cycle,
        "order": order,
        "current": current,
    }
