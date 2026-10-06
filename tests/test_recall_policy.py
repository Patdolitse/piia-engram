"""Unit tests for the central recall eligibility policy (pure, no store)."""

from __future__ import annotations

import pytest

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


def test_pending_cycle_stays_pending():
    idx = rp.build_supersede_index([
        {"src": "p1", "rel": "supersedes", "dst": "p2"},
        {"src": "p2", "rel": "supersedes", "dst": "p1"},
    ])
    assert idx.cycle_ids == frozenset({"p1", "p2"})
    assert rp.classify(_row("p1", tier="staging"), idx).state == rp.PENDING
    assert rp.classify(_row("p2", tier="staging"), idx).state == rp.PENDING


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
    assert idx.successor("a") == "x"
    assert idx.successor("b") == "" and idx.successor("c") == ""


def test_index_is_deterministic_for_two_successors():
    edges = [
        {"src": "z-new", "rel": "supersedes", "dst": "old"},
        {"src": "a-new", "rel": "supersedes", "dst": "old"},
    ]
    assert rp.build_supersede_index(edges).successor("old") == "a-new"
    assert rp.build_supersede_index(list(reversed(edges))).successor("old") == "a-new"


# --- classification (whitelist) --------------------------------------------


def test_classify_states():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    assert rp.classify(_row("ok"), idx).state == rp.TRUSTED
    assert rp.classify(_row("untiered", tier=None), idx).state == rp.TRUSTED
    assert rp.classify(_row("VERIFIED", tier="Verified"), idx).state == rp.TRUSTED
    assert rp.classify(_row("p", tier="staging"), idx).state == rp.PENDING
    assert rp.classify(_row("P", tier="Staging"), idx).state == rp.PENDING
    assert rp.classify(_row("ms", tier=None, memory_state="STAGING"), idx).state == rp.PENDING
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


@pytest.mark.parametrize("overrides,reason", [
    ({"tier": "unverified"}, "unknown_tier:unverified"),
    ({"memory_state": "rejected"}, "unknown_tier:rejected"),
    ({"memory_state": "deprecated"}, "unknown_tier:deprecated"),
    ({"approval_status": "deprecated"}, "unknown_tier:deprecated"),
    ({"approval_status": "Rejected"}, "unknown_tier:rejected"),
    ({"tier": "gold"}, "unknown_tier:gold"),
])
def test_whitelist_rejects_unknown_labels(overrides, reason):
    verdict = rp.classify(_row("x", **overrides))
    assert verdict.state == rp.ARCHIVED and verdict.reason == reason
    assert not rp.is_trusted(_row("x", **overrides))


@pytest.mark.parametrize("status", [None, "", "  ", "Archived", "pending"])
def test_missing_or_non_active_status_is_never_trusted(status):
    row = _row("x")
    if status is None:
        row.pop("status")
    else:
        row["status"] = status
    assert rp.classify(row).state == rp.ARCHIVED
    assert not rp.is_trusted(row)


def test_status_and_labels_are_case_insensitive():
    assert rp.classify(_row("x", status="Active", tier="VERIFIED")).state == rp.TRUSTED


def test_unknown_tier_beats_a_supersedes_edge():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "odd"}])
    assert rp.classify(_row("odd", tier="unverified"), idx).state == rp.ARCHIVED


def test_withheld_wins_over_every_other_state():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    for row in (_row("old"), _row("p", tier="staging"), _row("a", status="archived")):
        assert rp.classify(row, idx, withheld_reason="staging_excluded").state == rp.WITHHELD


def test_superseded_pending_row_is_superseded():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "p"}])
    assert rp.classify(_row("p", tier="staging"), idx).state == rp.SUPERSEDED


def test_is_trusted_ignores_edges_and_rejects_snapshots():
    assert rp.is_trusted(_row("ok"))
    assert not rp.is_trusted(_row("p", tier="staging"))
    assert not rp.is_trusted(_row("s", snapshot_of="h"))
    assert not rp.is_trusted("junk")


def test_partition_keeps_order_and_groups():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    rows = [_row("p1", tier="staging"), _row("t1"), _row("old"), _row("t2"),
            _row("a", status="archived"), "junk"]
    part = rp.partition(rows, idx)
    assert [r["id"] for r in part.trusted] == ["t1", "t2"]
    assert [r["id"] for r in part.pending] == ["p1"]
    assert [r["id"] for r in part.superseded] == ["old"]
    assert [r["id"] for r in part.archived] == ["a"]
    assert part.successor_of("old") == "new"


def test_trusted_only():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    rows = [_row("p", tier="staging"), _row("old"), _row("new"), _row("a", status="archived")]
    assert [r["id"] for r in rp.trusted_only(rows, idx)] == ["new"]


def test_marks_are_additive_copies():
    row = _row("p", tier="staging")
    marked = rp.mark_pending(row)
    assert marked["pending_untrusted"] is True and marked["eligibility"] == rp.PENDING
    assert "pending_untrusted" not in row
    sup = rp.mark_superseded(_row("old"), "new")
    assert sup["superseded_by"] == "new" and sup["eligibility"] == rp.SUPERSEDED


def test_label_by_verdict():
    idx = rp.build_supersede_index([{"src": "new", "rel": "supersedes", "dst": "old"}])
    out = rp.label({"id": "old"}, rp.classify(_row("old"), idx))
    assert out["eligibility"] == rp.SUPERSEDED and out["superseded_by"] == "new"
    out = rp.label({"id": "p"}, rp.classify(_row("p", tier="staging"), idx))
    assert out["eligibility"] == rp.PENDING and out["pending_untrusted"] is True
    out = rp.label({"id": "ok"}, rp.classify(_row("ok"), idx))
    assert out == {"id": "ok", "eligibility": rp.TRUSTED}


# --- budget omission ---------------------------------------------------------


def test_omitted_info_shape_and_empty():
    assert rp.omitted_info() is None
    info = rp.omitted_info(ids=["a", "b", "a"], sections=["lessons", "lessons", "tools"], extra=1)
    assert info == {"omitted_count": 3, "ids": ["a", "b"], "sections": ["lessons", "tools"],
                    "reason": "budget"}


def test_omission_line_languages():
    info = {"omitted_count": 3, "ids": ["a"], "sections": ["lessons", "tools"], "reason": "budget"}
    assert rp.omission_line(info) == "已省略 3 项（预算）：lessons, tools"
    assert rp.omission_line(info, lang="en") == "Omitted 3 items (budget): lessons, tools"
    one = {"omitted_count": 1, "ids": [], "sections": [], "reason": "budget"}
    assert rp.omission_line(one, lang="en") == "Omitted 1 item (budget)"
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
