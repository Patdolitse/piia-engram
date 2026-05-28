"""Integration: c0 decision-thread wired onto the Engram instance
(add_relation + get_decision_thread). Additive — existing reads unchanged.
"""

from __future__ import annotations

from piia_engram.core import Engram


def _ids(eng):
    d1 = eng.add_decision({"title": "choose test framework", "choice": "pytest"})
    d2 = eng.add_decision({"title": "add fixtures", "choice": "conftest"})
    return d1["id"], d2["id"]


def test_add_relation_and_get_decision_thread(tmp_path):
    eng = Engram(root=tmp_path)
    a, b = _ids(eng)

    assert eng.add_relation(a, "led_to", b)["added"] is True
    # idempotent
    assert eng.add_relation(a, "led_to", b)["added"] is False

    t = eng.get_decision_thread(a)
    assert t["found"] is True
    assert [r["id"] for r in t["order"]] == [a, b]
    assert t["heads"] == [b]
    # summaries pulled from the real entries
    summaries = {r["id"]: r.get("summary", "") for r in t["order"]}
    assert "pytest" in summaries[a] or "test framework" in summaries[a]


def test_add_relation_rejects_bad_rel(tmp_path):
    eng = Engram(root=tmp_path)
    a, b = _ids(eng)
    assert eng.add_relation(a, "bogus", b)["added"] is False


def test_add_relation_rejects_unknown_id(tmp_path):
    # Codex round-3 P2: don't pollute threads with edges to non-existent ids
    eng = Engram(root=tmp_path)
    a, _ = _ids(eng)
    res = eng.add_relation(a, "led_to", "no-such-id")
    assert res["added"] is False and res.get("reason") == "unknown_id"


def test_get_decision_thread_unknown_seed(tmp_path):
    eng = Engram(root=tmp_path)
    t = eng.get_decision_thread("does-not-exist")
    assert t["found"] is False


def test_relations_persist_on_disk(tmp_path):
    eng = Engram(root=tmp_path)
    a, b = _ids(eng)
    eng.add_relation(a, "implemented_by", b)
    # a fresh Engram on the same root sees the relation
    eng2 = Engram(root=tmp_path)
    t = eng2.get_decision_thread(a)
    assert [r["id"] for r in t["order"]] == [a, b]
