"""c1 decision-thread integration: remove_relation, auto-supersedes,
explicit supersedes, and thread evolution scenarios.

Additive to test_decision_thread_integration.py (c0 basics).
"""

from __future__ import annotations

import pytest

from piia_engram.core import Engram


# ── helpers ────────────────────────────────────────────────────────────────

def _decision(eng, question, choice, **kw):
    """Shorthand: add a decision and return its id."""
    d = eng.add_decision({"question": question, "choice": choice, **kw})
    return d["id"]


def _thread_order(eng, seed_id):
    """Return (ordered_ids, heads, active_ids) from the thread."""
    t = eng.get_decision_thread(seed_id)
    ids = [r["id"] for r in t.get("order", [])]
    return ids, t.get("heads", []), t.get("active_ids", [])


# ── remove_relation ────────────────────────────────────────────────────────

class TestRemoveRelation:
    def test_remove_existing(self, tmp_path):
        eng = Engram(root=tmp_path)
        a = _decision(eng, "framework", "django")
        b = _decision(eng, "orm", "sqlalchemy")
        eng.add_relation(a, "led_to", b)
        assert eng.remove_relation(a, "led_to", b)["removed"] is True
        # thread should show no link
        t = eng.get_decision_thread(a)
        assert t["found"] is False  # no edges → seed not in any thread

    def test_remove_idempotent(self, tmp_path):
        eng = Engram(root=tmp_path)
        a = _decision(eng, "framework", "django")
        b = _decision(eng, "orm", "sqlalchemy")
        # never added
        assert eng.remove_relation(a, "led_to", b)["removed"] is False

    def test_remove_one_edge_keeps_others(self, tmp_path):
        eng = Engram(root=tmp_path)
        a = _decision(eng, "step1", "do x")
        b = _decision(eng, "step2", "do y")
        c = _decision(eng, "step3", "do z")
        eng.add_relation(a, "led_to", b)
        eng.add_relation(b, "led_to", c)
        eng.remove_relation(a, "led_to", b)
        # a→b gone, but b→c still there
        t = eng.get_decision_thread(b)
        assert t["found"] is True
        assert a not in [r["id"] for r in t["order"]]


# ── auto-supersedes (same question, different choice) ──────────────────────

class TestAutoSupersedes:
    def test_different_choice_creates_supersedes_edge(self, tmp_path):
        eng = Engram(root=tmp_path)
        old_id = _decision(eng, "database choice", "MySQL")
        new_id = _decision(eng, "database choice", "PostgreSQL")
        # The new decision should auto-supersede the old one
        t = eng.get_decision_thread(new_id)
        assert t["found"] is True
        ids, heads, active = _thread_order(eng, new_id)
        assert old_id in ids
        assert new_id in ids
        # old is superseded, new is the head
        assert new_id in heads
        assert new_id in active
        assert old_id not in active  # superseded

    def test_same_choice_no_supersedes(self, tmp_path):
        """Same question + same choice → duplicate (returns early, no edge)."""
        eng = Engram(root=tmp_path)
        old = eng.add_decision({"question": "database choice", "choice": "MySQL"})
        dup = eng.add_decision({"question": "database choice", "choice": "MySQL"})
        assert dup.get("status") == "duplicate"
        # no thread should exist (no relation written)
        t = eng.get_decision_thread(old["id"])
        assert t["found"] is False

    def test_three_revisions_chain(self, tmp_path):
        """Three successive choice changes on the same question → linear chain."""
        eng = Engram(root=tmp_path)
        v1 = _decision(eng, "deploy target", "Heroku")
        v2 = _decision(eng, "deploy target", "AWS")
        v3 = _decision(eng, "deploy target", "Fly.io")
        ids, heads, active = _thread_order(eng, v1)
        # All three in the thread
        assert set(ids) == {v1, v2, v3}
        # Only v3 is active head
        assert heads == [v3]
        assert v1 not in active
        assert v2 not in active
        assert v3 in active


# ── explicit supersedes field ──────────────────────────────────────────────

class TestExplicitSupersedes:
    def test_explicit_supersedes_field(self, tmp_path):
        eng = Engram(root=tmp_path)
        old_id = _decision(eng, "ci provider", "Travis")
        # Different question text — dedup won't auto-detect; use explicit field
        new_id = _decision(eng, "ci/cd platform", "GitHub Actions",
                           supersedes=old_id)
        t = eng.get_decision_thread(new_id)
        assert t["found"] is True
        ids, heads, active = _thread_order(eng, new_id)
        assert old_id in ids
        assert new_id in heads
        assert old_id not in active  # superseded

    def test_explicit_supersedes_bad_id(self, tmp_path):
        """Explicit supersedes with a non-existent ID should not crash."""
        eng = Engram(root=tmp_path)
        d = eng.add_decision({"question": "test", "choice": "a",
                              "supersedes": "nonexistent-id"})
        # Decision is still written successfully
        assert d.get("id")
        # No thread (the edge was rejected by add_relation endpoint validation)
        t = eng.get_decision_thread(d["id"])
        assert t["found"] is False


# ── thread topology ────────────────────────────────────────────────────────

class TestThreadTopology:
    def test_diamond_thread(self, tmp_path):
        """A → B, A → C, B → D, C → D — diamond merge."""
        eng = Engram(root=tmp_path)
        a = _decision(eng, "step a", "x")
        b = _decision(eng, "step b", "y")
        c = _decision(eng, "step c", "z")
        d = _decision(eng, "step d", "w")
        eng.add_relation(a, "led_to", b)
        eng.add_relation(a, "led_to", c)
        eng.add_relation(b, "led_to", d)
        eng.add_relation(c, "led_to", d)
        ids, heads, active = _thread_order(eng, a)
        assert set(ids) == {a, b, c, d}
        assert heads == [d]  # d is the only head

    def test_implemented_by_ordering(self, tmp_path):
        """Decision → implementation via implemented_by edge."""
        eng = Engram(root=tmp_path)
        dec = _decision(eng, "use caching", "Redis")
        impl = eng.add_lesson({"summary": "implemented Redis caching"})
        eng.add_relation(dec, "implemented_by", impl["id"])
        ids, heads, _ = _thread_order(eng, dec)
        assert ids == [dec, impl["id"]]
        assert heads == [impl["id"]]

    def test_cycle_tolerance(self, tmp_path):
        """A cycle should not hang — order_thread appends remaining in sorted order."""
        eng = Engram(root=tmp_path)
        a = _decision(eng, "cycle a", "x")
        b = _decision(eng, "cycle b", "y")
        eng.add_relation(a, "led_to", b)
        eng.add_relation(b, "led_to", a)  # creates cycle
        t = eng.get_decision_thread(a)
        assert t["found"] is True
        assert t["has_cycle"] is True
        assert set(r["id"] for r in t["order"]) == {a, b}

    def test_cross_type_thread(self, tmp_path):
        """Thread can span lessons and decisions."""
        eng = Engram(root=tmp_path)
        lesson = eng.add_lesson({"summary": "observed slowness"})
        decision = eng.add_decision({"question": "fix slowness", "choice": "add cache"})
        eng.add_relation(lesson["id"], "led_to", decision["id"])
        ids, heads, _ = _thread_order(eng, lesson["id"])
        assert ids == [lesson["id"], decision["id"]]


# ── persistence ────────────────────────────────────────────────────────────

class TestPersistence:
    def test_remove_relation_persists(self, tmp_path):
        eng = Engram(root=tmp_path)
        a = _decision(eng, "q", "c1")
        b = _decision(eng, "q2", "c2")
        eng.add_relation(a, "led_to", b)
        eng.remove_relation(a, "led_to", b)
        # Fresh instance
        eng2 = Engram(root=tmp_path)
        t = eng2.get_decision_thread(a)
        assert t["found"] is False

    def test_auto_supersedes_persists(self, tmp_path):
        eng = Engram(root=tmp_path)
        _decision(eng, "lang", "Python")
        new_id = _decision(eng, "lang", "Rust")
        # Fresh instance
        eng2 = Engram(root=tmp_path)
        t = eng2.get_decision_thread(new_id)
        assert t["found"] is True
        assert new_id in t["heads"]
