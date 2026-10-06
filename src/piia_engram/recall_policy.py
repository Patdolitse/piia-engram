"""Recall eligibility policy: one place that decides what a recall surface may return.

Every recall entry point (cold-start context, resume brief, session hooks,
``get_recall``, ``get_relevant_knowledge``, ``search_knowledge``, the Memory
Lens preview, and by-id reads) classifies each candidate row here instead of
re-implementing its own tier / status / version checks.

Eligibility states
------------------
``trusted``     ``status=active`` and only reviewed labels: ``tier`` and
                ``memory_state`` empty or ``verified``, ``approval_status``
                not rejected / deprecated (a whitelist, case-insensitive)
``pending``     waiting for the Owner's review (``tier`` or ``memory_state``
                is ``staging``)
``superseded``  replaced by a newer version: a version snapshot, or the target
                of an honored ``supersedes`` edge (a row that is not reviewed
                never hides a reviewed one; the caller passes honored edges)
``archived``    everything else: a missing or non-active status, an archived
                or unknown tier, a rejected or deprecated approval
``withheld``    kept back by governance or a sensitivity ceiling (decided by
                the caller; this module only records the reason)

Uses
----
``auto_inject``      context the AI receives without asking: trusted only
``explicit_search``  a search the AI asked for: trusted results, plus a separate
                     pending group; superseded only on request, also separate
``by_id``            reading one known id: every state but withheld, annotated

A cycle of ``supersedes`` edges is not a version order. Edges inside a cycle
are ignored, so its members keep their own state; the caller logs one audit
warning (see ``Engram._recall_supersede_index``).

Budget omissions are reported as ``{omitted_count, ids, sections, reason}``
with no content of the dropped rows. Pure module: stdlib only, no store access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

TRUSTED = "trusted"
PENDING = "pending"
SUPERSEDED = "superseded"
ARCHIVED = "archived"
WITHHELD = "withheld"
STATES = (TRUSTED, PENDING, SUPERSEDED, ARCHIVED, WITHHELD)


OMIT_REASON_BUDGET = "budget"


# ---------------------------------------------------------------------------
# supersede index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupersedeIndex:
    """Old id -> the id that supersedes it, plus the ids caught in a cycle."""

    superseded_by: Mapping[str, str] = field(default_factory=dict)
    cycle_ids: frozenset = frozenset()

    def successor(self, item_id: Any) -> str:
        if not isinstance(item_id, str):
            return ""
        return self.superseded_by.get(item_id, "")


EMPTY_INDEX = SupersedeIndex()


def _supersede_pairs(edges: Iterable[Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for edge in edges or ():
        if not isinstance(edge, dict) or str(edge.get("rel", "")).strip() != "supersedes":
            continue
        src, dst = edge.get("src"), edge.get("dst")
        if not src or not dst:
            continue
        src, dst = str(src), str(dst)
        if src != dst:
            pairs.append((src, dst))
    return pairs


def _strongly_connected(nodes: set[str], adj: dict[str, list[str]]) -> dict[str, int]:
    """Iterative Tarjan: node -> component number (no recursion limit)."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    comp_no = 0
    for root in sorted(nodes):
        if root in index_of:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbours = adj.get(node, [])
            if i < len(neighbours):
                work.append((node, i + 1))
                nxt = neighbours[i]
                if nxt not in index_of:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index_of[nxt])
                continue
            for nxt in neighbours:
                if nxt in on_stack:
                    low[node] = min(low[node], low[nxt])
            if low[node] == index_of[node]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp[member] = comp_no
                    if member == node:
                        break
                comp_no += 1
    return comp


def build_supersede_index(edges: Iterable[Any]) -> SupersedeIndex:
    """Build the index from (honored) relation edges. Never raises on bad edges."""
    pairs = _supersede_pairs(edges)
    if not pairs:
        return EMPTY_INDEX
    nodes: set[str] = set()
    adj: dict[str, list[str]] = {}
    for src, dst in pairs:
        nodes.update((src, dst))
        adj.setdefault(src, [])
        if dst not in adj[src]:
            adj[src].append(dst)
    for targets in adj.values():
        targets.sort()
    comp = _strongly_connected(nodes, adj)
    sizes: dict[int, int] = {}
    for number in comp.values():
        sizes[number] = sizes.get(number, 0) + 1
    cycle_ids = frozenset(n for n, number in comp.items() if sizes[number] > 1)
    superseded_by: dict[str, str] = {}
    for src, dst in sorted(pairs):
        if src in cycle_ids and comp.get(src) == comp.get(dst):
            continue  # an edge inside a cycle orders nothing
        superseded_by.setdefault(dst, src)
    return SupersedeIndex(superseded_by=superseded_by, cycle_ids=cycle_ids)


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Eligibility:
    state: str
    superseded_by: str = ""
    reason: str = ""


# Whitelist of the labels a reviewed row may carry. Anything else (an unknown
# tier, a rejected or deprecated approval) is never trusted.
_TRUSTED_TIERS = frozenset({"", "verified"})
_TRUSTED_MEMORY_STATES = frozenset({"", "verified"})
_UNTRUSTED_APPROVALS = frozenset({"rejected", "deprecated"})


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_version_snapshot(row: Mapping[str, Any]) -> bool:
    return bool(row.get("snapshot_of")) or _norm(row.get("status")) == "superseded"


def _review_state(row: Mapping[str, Any]) -> Eligibility:
    """Pending / trusted / archived from the row's own labels (no edges)."""
    status = _norm(row.get("status"))
    if status != "active":
        return Eligibility(ARCHIVED, reason=f"status:{status}" if status else "status_missing")
    tier = _norm(row.get("tier"))
    memory_state = _norm(row.get("memory_state"))
    if tier == "staging" or memory_state == "staging":
        return Eligibility(PENDING, reason="awaiting_review")
    approval = _norm(row.get("approval_status"))
    if (
        tier in _TRUSTED_TIERS
        and memory_state in _TRUSTED_MEMORY_STATES
        and approval not in _UNTRUSTED_APPROVALS
    ):
        return Eligibility(TRUSTED)
    if tier not in _TRUSTED_TIERS:
        value = tier
    elif memory_state not in _TRUSTED_MEMORY_STATES:
        value = memory_state
    else:
        value = approval
    return Eligibility(ARCHIVED, reason=f"unknown_tier:{value}")


def classify(
    row: Mapping[str, Any],
    index: SupersedeIndex = EMPTY_INDEX,
    *,
    withheld_reason: str = "",
) -> Eligibility:
    """Eligibility of one stored row. ``withheld_reason`` comes from the caller.

    Order: withheld; version snapshot (superseded); the row's own labels
    (status must be ``active``; ``tier`` / ``memory_state`` ``staging`` is
    pending; only the reviewed whitelist is trusted; everything else is
    archived); then a ``supersedes`` edge turns a pending or trusted row into
    superseded. Labels are compared case-insensitively.
    """
    if withheld_reason:
        return Eligibility(WITHHELD, reason=str(withheld_reason))
    if not isinstance(row, Mapping):
        return Eligibility(ARCHIVED, reason="malformed")
    if _is_version_snapshot(row):
        successor = str(row.get("superseded_by") or row.get("snapshot_of") or "")
        return Eligibility(SUPERSEDED, superseded_by=successor, reason="version_snapshot")
    own = _review_state(row)
    if own.state == ARCHIVED:
        return own
    successor = index.successor(row.get("id"))
    if successor:
        return Eligibility(SUPERSEDED, superseded_by=successor, reason="supersedes_edge")
    return own


def is_trusted(row: Mapping[str, Any]) -> bool:
    """Whether a row's own labels make it trusted (edges not considered)."""
    if not isinstance(row, Mapping) or _is_version_snapshot(row):
        return False
    return _review_state(row).state == TRUSTED


@dataclass
class Partition:
    trusted: list = field(default_factory=list)
    pending: list = field(default_factory=list)
    superseded: list = field(default_factory=list)
    archived: list = field(default_factory=list)
    successors: dict = field(default_factory=dict)

    def successor_of(self, item_id: Any) -> str:
        return self.successors.get(item_id, "") if isinstance(item_id, str) else ""

    def group(self, state: str) -> list:
        return getattr(self, state)


def partition(rows: Iterable[Any], index: SupersedeIndex = EMPTY_INDEX) -> Partition:
    """Split ``rows`` by state, keeping input order inside each group."""
    out = Partition()
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        verdict = classify(row, index)
        out.group(verdict.state).append(row)
        if verdict.state == SUPERSEDED and isinstance(row.get("id"), str):
            out.successors[row["id"]] = verdict.superseded_by
    return out


def trusted_only(rows: Iterable[Any], index: SupersedeIndex = EMPTY_INDEX) -> list:
    """What an auto-injected context may carry: the trusted rows, in order."""
    return list(partition(rows, index).trusted)


# ---------------------------------------------------------------------------
# annotations (additive copies, never in place)
# ---------------------------------------------------------------------------


def mark_pending(view: Mapping[str, Any]) -> dict:
    out = dict(view)
    out["eligibility"] = PENDING
    out["pending_untrusted"] = True
    return out


def mark_superseded(view: Mapping[str, Any], successor: str) -> dict:
    out = dict(view)
    out["eligibility"] = SUPERSEDED
    out["superseded_by"] = str(successor or "")
    return out


def label(view: Mapping[str, Any], verdict: Eligibility) -> dict:
    """Copy of ``view`` carrying ``verdict`` (by-id reads return every state)."""
    if verdict.state == PENDING:
        return mark_pending(view)
    if verdict.state == SUPERSEDED:
        return mark_superseded(view, verdict.superseded_by)
    out = dict(view)
    out["eligibility"] = verdict.state
    return out


# ---------------------------------------------------------------------------
# budget omission (ids and section names only, never content)
# ---------------------------------------------------------------------------


def _unique(values: Iterable[Any]) -> list[str]:
    seen: list[str] = []
    for value in values or ():
        text = str(value or "")
        if text and text not in seen:
            seen.append(text)
    return seen


def omitted_info(
    *,
    ids: Iterable[Any] = (),
    sections: Iterable[Any] = (),
    extra: int = 0,
) -> dict | None:
    """``{omitted_count, ids, sections, reason}`` or None when nothing was dropped.

    ``omitted_count`` counts each dropped id once plus ``extra`` dropped pieces
    that carry no id (for example a whole profile or tools section). The only
    reason is ``budget``: a fixed item cap is not reported as an omission.
    """
    id_list = _unique(ids)
    count = len(id_list) + max(0, int(extra))
    if count <= 0:
        return None
    return {
        "omitted_count": count,
        "ids": id_list,
        "sections": _unique(sections),
        "reason": OMIT_REASON_BUDGET,
    }


def merge_omitted(*infos: dict | None) -> dict | None:
    present = [i for i in infos if isinstance(i, dict) and i.get("omitted_count")]
    if not present:
        return None
    if len(present) == 1:
        return dict(present[0])
    ids: list[Any] = []
    sections: list[Any] = []
    extra = 0
    for info in present:
        info_ids = _unique(info.get("ids") or ())
        ids.extend(info_ids)
        sections.extend(info.get("sections") or ())
        extra += max(0, int(info.get("omitted_count") or 0) - len(info_ids))
    return omitted_info(ids=ids, sections=sections, extra=extra)


def omission_line(omitted: Mapping[str, Any] | None, lang: str = "zh") -> str:
    """The one text line a text-form context ends with when the budget cut it.

    ``lang`` follows the surrounding headings: ``zh`` for the cold-start
    context, ``en`` for the resume brief and the session-start hooks.
    """
    if not isinstance(omitted, Mapping):
        return ""
    count = int(omitted.get("omitted_count") or 0)
    if count <= 0:
        return ""
    names = ", ".join(str(s) for s in omitted.get("sections") or () if s)
    if lang == "en":
        noun = "item" if count == 1 else "items"
        head = f"Omitted {count} {noun} (budget)"
        return f"{head}: {names}" if names else head
    return f"已省略 {count} 项（预算）：{names}" if names else f"已省略 {count} 项（预算）"
