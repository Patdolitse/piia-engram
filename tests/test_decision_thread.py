"""Tests for c0 decision-thread scaffold (src/piia_engram/decision_thread.py)."""

from __future__ import annotations

from piia_engram import decision_thread as dt


def _edge(src, rel, dst):
    return {"src": src, "rel": rel, "dst": dst}


# ── edge validation ──────────────────────────────────────────────────────


def test_validate_drops_unknown_rel_and_selfloops_and_incomplete():
    edges = [
        _edge("a", "led_to", "b"),
        _edge("a", "bogus", "b"),       # unknown rel
        _edge("a", "led_to", "a"),      # self loop
        {"src": "a", "rel": "led_to"},  # missing dst
    ]
    out = dt.validate_edges(edges)
    assert out == [{"src": "a", "rel": "led_to", "dst": "b"}]


def test_validate_handles_none_and_non_dict_edges():
    # regression: must not raise AttributeError on None / str / int edges
    edges = [None, "x", 42, {"src": "a", "rel": "led_to", "dst": "b"}]
    out = dt.validate_edges(edges)
    assert out == [{"src": "a", "rel": "led_to", "dst": "b"}]


# ── ordering ─────────────────────────────────────────────────────────────


def test_linear_led_to_chain_orders_in_sequence():
    edges = [_edge("idea", "led_to", "plan"), _edge("plan", "led_to", "decision")]
    t = dt.build_thread("plan", edges)
    assert [r["id"] for r in t["order"]] == ["idea", "plan", "decision"]
    assert t["has_cycle"] is False
    assert t["found"] is True


def test_implemented_by_orders_decision_before_implementation():
    edges = [_edge("decision", "implemented_by", "pr_42")]
    t = dt.build_thread("decision", edges)
    assert [r["id"] for r in t["order"]] == ["decision", "pr_42"]


# ── supersedes ───────────────────────────────────────────────────────────


def test_supersedes_marks_old_and_head_is_new():
    edges = [_edge("v2", "supersedes", "v1"), _edge("v1", "led_to", "v2")]
    t = dt.build_thread("v1", edges)
    status = {r["id"]: r["status"] for r in t["order"]}
    assert status["v1"] == "superseded"
    assert status["v2"] == "active"
    assert t["active_ids"] == ["v2"]
    assert t["heads"] == ["v2"]


def test_supersedes_only_orders_old_before_new():
    # P3 fix: a lone "new supersedes old" must order [old, new], not lexicographic
    edges = [_edge("new", "supersedes", "old")]
    t = dt.build_thread("old", edges)
    assert [r["id"] for r in t["order"]] == ["old", "new"]
    assert t["heads"] == ["new"]
    assert {r["id"]: r["status"] for r in t["order"]}["old"] == "superseded"


def test_heads_is_tip_not_all_active():
    # linear chain: active_ids = all three, but head = only the tip
    edges = [_edge("idea", "led_to", "plan"), _edge("plan", "led_to", "decision")]
    t = dt.build_thread("plan", edges)
    assert t["active_ids"] == ["idea", "plan", "decision"]
    assert t["heads"] == ["decision"]


# ── connected component scoping ──────────────────────────────────────────


def test_thread_only_includes_topic_nodes_not_unrelated():
    edges = [
        _edge("a", "led_to", "b"),         # topic 1
        _edge("x", "led_to", "y"),         # unrelated topic 2
    ]
    t = dt.build_thread("a", edges)
    ids = {r["id"] for r in t["order"]}
    assert ids == {"a", "b"}
    assert "x" not in ids and "y" not in ids


def test_unknown_seed_returns_not_found():
    t = dt.build_thread("nope", [_edge("a", "led_to", "b")])
    assert t["found"] is False
    assert t["order"] == [] and t["active_ids"] == [] and t["heads"] == []


# ── cycle safety (must not hang) ─────────────────────────────────────────


def test_cycle_is_flagged_and_does_not_hang():
    edges = [_edge("a", "led_to", "b"), _edge("b", "led_to", "c"), _edge("c", "led_to", "a")]
    t = dt.build_thread("a", edges)
    assert t["has_cycle"] is True
    assert {r["id"] for r in t["order"]} == {"a", "b", "c"}  # all still present


# ── entries enrichment ───────────────────────────────────────────────────


def test_order_includes_summary_when_entries_given():
    edges = [_edge("d1", "led_to", "d2")]
    entries = {"d1": {"summary": "chose pytest"}, "d2": {"title": "added fixtures"}}
    t = dt.build_thread("d1", edges, entries=entries)
    by_id = {r["id"]: r for r in t["order"]}
    assert by_id["d1"]["summary"] == "chose pytest"
    assert by_id["d2"]["summary"] == "added fixtures"


def test_relation_types_are_the_three_agreed():
    assert set(dt.RELATION_TYPES) == {"led_to", "supersedes", "implemented_by"}
