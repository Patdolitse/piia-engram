"""c2 decision-thread integration: get_decision_history — query by
question text, chronological revision timeline, current active decision.

Additive to test_decision_thread_c1.py (c1: auto-supersedes + remove_relation).
"""

from __future__ import annotations

import pytest

from piia_engram.core import Engram


# ── helpers ────────────────────────────────────────────────────────────────

def _decision(eng, question, choice, **kw):
    """Shorthand: add a decision and return its id."""
    d = eng.add_decision({"question": question, "choice": choice, **kw})
    return d["id"]


# ── basic lookup ──────────────────────────────────────────────────────────

class TestDecisionHistoryBasic:
    def test_no_match(self, tmp_path):
        eng = Engram(root=tmp_path)
        _decision(eng, "database choice", "MySQL")
        h = eng.get_decision_history("totally unrelated query")
        assert h["found"] is False
        assert h["revisions"] == []
        assert h["current"] is None
        assert h["revision_count"] == 0

    def test_single_decision(self, tmp_path):
        eng = Engram(root=tmp_path)
        did = _decision(eng, "database choice", "MySQL")
        h = eng.get_decision_history("database choice")
        assert h["found"] is True
        assert h["revision_count"] == 1
        assert h["revisions"][0]["id"] == did
        assert h["revisions"][0]["choice"] == "MySQL"
        assert h["revisions"][0]["status"] == "active"
        assert h["current"]["id"] == did

    def test_exact_question_match(self, tmp_path):
        """Exact same question text should always match."""
        eng = Engram(root=tmp_path)
        did = _decision(eng, "which CI provider", "GitHub Actions")
        h = eng.get_decision_history("which CI provider")
        assert h["found"] is True
        assert h["current"]["choice"] == "GitHub Actions"

    def test_partial_match_above_threshold(self, tmp_path):
        """Similar question text should match at a reasonable threshold."""
        eng = Engram(root=tmp_path)
        _decision(eng, "database choice for production", "PostgreSQL")
        # Use a threshold low enough to accept partial overlap
        h = eng.get_decision_history("database choice for production", threshold=0.6)
        assert h["found"] is True
        assert h["revision_count"] >= 1

    def test_threshold_filters(self, tmp_path):
        """High threshold should reject loose matches."""
        eng = Engram(root=tmp_path)
        _decision(eng, "database choice", "MySQL")
        # Very strict threshold — even a near-match might not pass
        h = eng.get_decision_history("db", threshold=0.99)
        assert h["found"] is False


# ── revision history ──────────────────────────────────────────────────────

class TestRevisionHistory:
    def test_two_revisions_chronological(self, tmp_path):
        eng = Engram(root=tmp_path)
        v1 = _decision(eng, "deploy target", "Heroku")
        v2 = _decision(eng, "deploy target", "AWS")
        h = eng.get_decision_history("deploy target")
        assert h["found"] is True
        assert h["revision_count"] == 2
        # Oldest first
        assert h["revisions"][0]["id"] == v1
        assert h["revisions"][1]["id"] == v2
        # v1 is superseded, v2 is active
        assert h["revisions"][0]["status"] == "superseded"
        assert h["revisions"][1]["status"] == "active"
        assert h["revisions"][0]["superseded_by"] == v2
        # current points to v2
        assert h["current"]["id"] == v2
        assert h["current"]["choice"] == "AWS"

    def test_three_revisions_chain(self, tmp_path):
        eng = Engram(root=tmp_path)
        v1 = _decision(eng, "language", "Python")
        v2 = _decision(eng, "language", "Go")
        v3 = _decision(eng, "language", "Rust")
        h = eng.get_decision_history("language")
        assert h["revision_count"] == 3
        ids = [r["id"] for r in h["revisions"]]
        assert ids == [v1, v2, v3]
        # Only v3 is active
        statuses = [r["status"] for r in h["revisions"]]
        assert statuses == ["superseded", "superseded", "active"]
        assert h["current"]["id"] == v3

    def test_reasoning_included(self, tmp_path):
        eng = Engram(root=tmp_path)
        _decision(eng, "test framework", "pytest",
                  reasoning="better fixture system")
        h = eng.get_decision_history("test framework")
        assert h["revisions"][0]["reasoning"] == "better fixture system"


# ── multiple unrelated decisions ──────────────────────────────────────────

class TestIsolation:
    def test_unrelated_decisions_excluded(self, tmp_path):
        """Decisions on different topics should not appear."""
        eng = Engram(root=tmp_path)
        _decision(eng, "database choice", "MySQL")
        _decision(eng, "CI provider", "GitHub Actions")
        h = eng.get_decision_history("database choice")
        assert h["revision_count"] == 1
        assert h["revisions"][0]["choice"] == "MySQL"

    def test_similar_but_different_topics(self, tmp_path):
        """Similar but distinct questions with high threshold."""
        eng = Engram(root=tmp_path)
        _decision(eng, "backend database", "PostgreSQL")
        _decision(eng, "frontend framework", "React")
        h = eng.get_decision_history("backend database", threshold=0.8)
        assert h["revision_count"] == 1
        assert h["revisions"][0]["choice"] == "PostgreSQL"


# ── edge cases ────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_knowledge(self, tmp_path):
        eng = Engram(root=tmp_path)
        h = eng.get_decision_history("anything")
        assert h["found"] is False

    def test_low_threshold_broadens(self, tmp_path):
        """Very low threshold should match more liberally."""
        eng = Engram(root=tmp_path)
        _decision(eng, "which database to use", "PostgreSQL")
        # "database to use" shares enough bigrams with the stored text
        h = eng.get_decision_history("database to use", threshold=0.3)
        assert h["found"] is True

    def test_query_field_preserved(self, tmp_path):
        eng = Engram(root=tmp_path)
        h = eng.get_decision_history("my specific question")
        assert h["query"] == "my specific question"


# ── persistence ───────────────────────────────────────────────────────────

class TestHistoryPersistence:
    def test_history_survives_reload(self, tmp_path):
        eng = Engram(root=tmp_path)
        _decision(eng, "cache strategy", "Redis")
        _decision(eng, "cache strategy", "Memcached")
        # Fresh instance
        eng2 = Engram(root=tmp_path)
        h = eng2.get_decision_history("cache strategy")
        assert h["found"] is True
        assert h["revision_count"] == 2
        assert h["current"]["choice"] == "Memcached"

    def test_explicit_supersedes_reflected(self, tmp_path):
        """Explicit supersedes (different question text) should show up."""
        eng = Engram(root=tmp_path)
        old_id = _decision(eng, "ci provider", "Travis")
        new_id = _decision(eng, "ci/cd platform", "GitHub Actions",
                           supersedes=old_id)
        # Query for the NEW question — should find the new decision
        h = eng.get_decision_history("ci/cd platform")
        assert h["found"] is True
        assert h["current"]["choice"] == "GitHub Actions"
        # But querying the OLD question should still find the old one
        h_old = eng.get_decision_history("ci provider")
        assert h_old["found"] is True
        # The old one is superseded (the edge exists in relations)
        assert h_old["revisions"][0]["status"] == "superseded"
        assert h_old["revisions"][0]["superseded_by"] == new_id
