"""Unit tests for the central recall eligibility policy (pure, no store)."""

from __future__ import annotations

from piia_engram import recall_policy as rp


def _row(rid, **kw):
    row = {"id": rid, "summary": f"summary {rid}", "status": "active", "tier": "verified"}
    row.update(kw)
    return row


# --- supersede index --------------------------------------------------------


def test_index_maps_old_to_successor():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    assert idx.successor("old") == "new"
    assert idx.successor("new") == ""
    assert idx.cycle_ids == frozenset()


def test_index_ignores_other_relations_and_malformed_edges():
    idx = rp.build_supersede_index([
        {"src": "a", "rel": "led_to", "dst": "b"},
        {"src": "", "rel": "supersedes", "dst": "c"},
        {"src": "d", "rel": "supersedes", "dst": "d"},
        None,
        "junk",
    ])
    assert dict(idx.superseded_by) == {}


def test_two_node_cycle_supersedes_nobody():
    idx = rp.build_supersede_index([
        {"src": "a", "rel": "supersedes", "dst": "b"},
        {"src": "b", "rel": "supersedes", "dst": "a"},
    ])
    assert idx.cycle_ids == frozenset({"a", "b"})
    assert idx.successor("a") == "" and idx.successor("b") == ""
    assert rp.classify(_row("a"), idx).state == rp.TRUSTED
    assert rp.classify(_row("b"), idx).state == rp.TRUSTED


def test_longer_cycle_and_edge_out_of_cycle():
    idx = rp.build_supersede_index([
        {"src": "a", "rel": "supersedes", "dst": "b"},
        {"src": "b", "rel": "supersedes", "dst": "c"},
        {"src": "c", "rel": "supersedes", "dst": "a"},
        {"src": "a", "rel": "supersedes", "dst": "old"},  # leaves the cycle
        {"src": "x", "rel": "supersedes", "dst": "a"},    # enters the cycle
    ])
    assert idx.cycle_ids == frozenset({"a", "b", "c"})
    assert idx.successor("old") == "a"
    # an edge from outside the cycle still supersedes its target
    assert idx.successor("a") == "x"
    assert idx.successor("b") == "" and idx.successor("c") == ""


def test_index_is_deterministic_for_two_successors():
    edges = [
        {"src": "z-new", "rel": "supersedes", "dst": "old"},
        {"src": "a-new", "rel": "supersedes", "dst": "old"},
    ]
    assert rp.build_supersede_index(edges).successor("old") == "a-new"
    assert rp.build_supersede_index(list(reversed(edges))).successor("old") == "a-new"


# --- classification ---------------------------------------------------------


def test_classify_states():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    assert rp.classify(_row("ok"), idx).state == rp.TRUSTED
    assert rp.classify(_row("legacy", tier=None), idx).state == rp.TRUSTED
    assert rp.classify({"id": "bare", "summary": "s"}, idx).state == rp.TRUSTED
    assert rp.classify(_row("p", tier="staging"), idx).state == rp.PENDING
    assert rp.classify(_row("ms", tier=None, memory_state="staging"), idx).state == rp.PENDING
    old = rp.classify(_row("old"), idx)
    assert old.state == rp.SUPERSEDED and old.superseded_by == "new"
    assert rp.classify(_row("a", status="archived"), idx).state == rp.ARCHIVED
    assert rp.classify(_row("o", status="outdated"), idx).state == rp.ARCHIVED
    assert rp.classify(_row("soft", tier="archived"), idx).state == rp.ARCHIVED
    snap = rp.classify(
        _row("h-prev-v1", status="superseded", tier="archived", snapshot_of="h", superseded_by="h"),
        idx,
    )
    assert snap.state == rp.SUPERSEDED and snap.superseded_by == "h"
    held = rp.classify(_row("ok"), idx, withheld_reason="sensitivity_above_ceiling")
    assert held.state == rp.WITHHELD and held.reason == "sensitivity_above_ceiling"


def test_withheld_wins_over_every_other_state():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    for row in (_row("old"), _row("p", tier="staging"), _row("a", status="archived")):
        assert rp.classify(row, idx, withheld_reason="staging_excluded").state == rp.WITHHELD


def test_superseded_pending_row_is_superseded():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "p"}])
    assert rp.classify(_row("p", tier="staging"), idx).state == rp.SUPERSEDED


# --- use admission ----------------------------------------------------------


def test_admits_per_use():
    assert rp.admits(rp.AUTO_INJECT, rp.TRUSTED)
    for state in (rp.PENDING, rp.SUPERSEDED, rp.ARCHIVED, rp.WITHHELD):
        assert not rp.admits(rp.AUTO_INJECT, state)
    assert rp.admits(rp.EXPLICIT_SEARCH, rp.TRUSTED)
    assert rp.admits(rp.EXPLICIT_SEARCH, rp.PENDING)
    assert not rp.admits(rp.EXPLICIT_SEARCH, rp.SUPERSEDED)
    assert rp.admits(rp.EXPLICIT_SEARCH, rp.SUPERSEDED, include_superseded=True)
    assert not rp.admits(rp.EXPLICIT_SEARCH, rp.ARCHIVED)
    assert not rp.admits(rp.EXPLICIT_SEARCH, rp.WITHHELD)
    for state in (rp.TRUSTED, rp.PENDING, rp.SUPERSEDED, rp.ARCHIVED):
        assert rp.admits(rp.BY_ID, state)
    assert not rp.admits(rp.BY_ID, rp.WITHHELD)


def test_partition_keeps_order_and_groups():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    rows = [_row("p1", tier="staging"), _row("t1"), _row("old"), _row("t2"),
            _row("a", status="archived"), _row("s", sensitivity="secret")]
    part = rp.partition(rows, idx, withheld=lambda r: "too_secret" if r.get("sensitivity") == "secret" else "")
    assert [r["id"] for r in part.trusted] == ["t1", "t2"]
    assert [r["id"] for r in part.pending] == ["p1"]
    assert [r["id"] for r in part.superseded] == ["old"]
    assert [r["id"] for r in part.archived] == ["a"]
    assert [r["id"] for r in part.withheld] == ["s"]
    assert part.successor_of("old") == "new"


def test_eligible_auto_inject_is_trusted_only():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    rows = [_row("p", tier="staging"), _row("old"), _row("new"), _row("a", status="archived")]
    assert [r["id"] for r in rp.eligible(rows, rp.AUTO_INJECT, idx)] == ["new"]


def test_marks_are_additive_copies():
    row = _row("p", tier="staging")
    marked = rp.mark_pending(row)
    assert marked["pending_untrusted"] is True and marked["eligibility"] == rp.PENDING
    assert "pending_untrusted" not in row
    sup = rp.mark_superseded(_row("old"), "new")
    assert sup["superseded_by"] == "new" and sup["eligibility"] == rp.SUPERSEDED


def test_annotate_by_id():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    out = rp.annotate_by_id(_row("old"), idx)
    assert out["eligibility"] == rp.SUPERSEDED and out["superseded_by"] == "new"
    out = rp.annotate_by_id(_row("p", tier="staging"), idx)
    assert out["eligibility"] == rp.PENDING and out["pending_untrusted"] is True
    out = rp.annotate_by_id(_row("ok"), idx)
    assert out["eligibility"] == rp.TRUSTED and "superseded_by" not in out


# --- budget omission ---------------------------------------------------------


def test_omitted_info_shape_and_empty():
    assert rp.omitted_info() is None
    info = rp.omitted_info(ids=["a", "b", "a"], sections=["lessons", "lessons", "tools"], extra=1)
    assert info == {"omitted_count": 3, "ids": ["a", "b"], "sections": ["lessons", "tools"],
                    "reason": "budget"}


def test_omission_line_names_count_and_sections():
    line = rp.omission_line({"omitted_count": 3, "ids": ["a"], "sections": ["lessons", "tools"],
                             "reason": "budget"})
    assert line == "已省略 3 项（预算）：lessons, tools"
    assert rp.omission_line(None) == ""
    assert rp.omission_line({"omitted_count": 0, "ids": [], "sections": [], "reason": "budget"}) == ""


def test_merge_omitted():
    a = rp.omitted_info(ids=["x"], sections=["lessons"])
    b = rp.omitted_info(sections=["matched_playbooks"], ids=["pb1"])
    merged = rp.merge_omitted(a, b)
    assert merged == {"omitted_count": 2, "ids": ["x", "pb1"],
                      "sections": ["lessons", "matched_playbooks"], "reason": "budget"}
    assert rp.merge_omitted(None, None) is None
    assert rp.merge_omitted(a, None) == a
