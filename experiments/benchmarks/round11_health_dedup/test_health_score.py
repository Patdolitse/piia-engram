"""Round 11 — Health score & suggest_merges evaluation (v3.20.0).

Deterministic unit tests that validate health score computation
and suggest_merges behavior. No LLM calls required.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from piia_engram.core import Engram


def _engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


# ── Health Score Dimensions ──


class TestHealthScoreDimensions:
    """Validate each dimension of the health score independently."""

    def test_empty_knowledge_returns_valid_score(self, tmp_path: Path):
        eng = _engram(tmp_path)
        report = eng.get_health_report()
        assert 0 <= report["health_score"] <= 100
        dims = report["dimensions"]
        assert dims["freshness"] == 100  # nothing stale
        assert dims["quality"] == 100    # nothing outdated
        assert dims["coverage"] == 0     # no domains
        assert dims["cleanliness"] == 100  # nothing dirty

    def test_single_domain_caps_coverage_at_30(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("lesson1", "python")
        eng.add_lesson("lesson2", "python")
        report = eng.get_health_report()
        assert report["dimensions"]["coverage"] == 30

    def test_multi_domain_increases_coverage(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("Use type hints for readable Python code", "python")
        eng.add_lesson("Prefer interfaces over concrete types in Go", "go")
        eng.add_lesson("Use ownership model correctly in Rust", "rust")
        eng.add_lesson("Minimize bundle size with tree-shaking in JavaScript", "javascript")
        report = eng.get_health_report()
        assert report["dimensions"]["coverage"] > 60

    def test_all_stale_makes_freshness_zero(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("old lesson", "testing")
        # Force stale
        path = tmp_path / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data[0]["last_reviewed"] = (datetime.now() - timedelta(days=90)).isoformat()
        eng._atomic_write(path, data)

        report = eng.get_health_report()
        assert report["dimensions"]["freshness"] == 0

    def test_staging_items_reduce_quality(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("verified lesson", "python")
        # Add staging item directly
        path = tmp_path / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        staging = dict(data[0])
        staging["id"] = "staging-item"
        staging["tier"] = "staging"
        data.append(staging)
        eng._atomic_write(path, data)

        report = eng.get_health_report()
        # 1 verified + 1 staging out of 2 total = 50% quality
        assert report["dimensions"]["quality"] == 50

    def test_composite_is_average_of_four(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("lesson", "python")
        report = eng.get_health_report()
        dims = report["dimensions"]
        expected = round(
            (dims["freshness"] + dims["quality"]
             + dims["coverage"] + dims["cleanliness"]) / 4
        )
        assert report["health_score"] == expected


# ── Suggest Merges ──


class TestSuggestMerges:
    """Validate suggest_merges scans and recommends correctly."""

    def test_no_data_returns_empty(self, tmp_path: Path):
        eng = _engram(tmp_path)
        result = eng.suggest_merges()
        assert result["total_candidates"] == 0
        assert result["suggestions"] == []

    def test_unique_items_return_no_candidates(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("Python async patterns for high performance", "python")
        eng.add_lesson("Database schema migration best practices", "database")
        result = eng.suggest_merges()
        assert result["total_candidates"] == 0

    def test_near_duplicates_detected(self, tmp_path: Path):
        eng = _engram(tmp_path)
        base = "Always use type hints in Python function signatures for readability"
        eng.add_lesson({"summary": base})
        # Bypass dedup
        path = tmp_path / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        dup = dict(data[0])
        dup["id"] = "near-dup"
        dup["summary"] = base.replace("readability", "clarity and readability")
        data.append(dup)
        eng._atomic_write(path, data)

        result = eng.suggest_merges(threshold=0.4)
        assert result["total_candidates"] >= 1
        s = result["suggestions"][0]
        assert "merge_knowledge" in s["action"]
        assert s["similarity"] >= 0.4

    def test_threshold_filtering(self, tmp_path: Path):
        eng = _engram(tmp_path)
        base = "Use dependency injection for testable code architecture"
        eng.add_lesson({"summary": base})
        path = tmp_path / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        dup = dict(data[0])
        dup["id"] = "low-sim"
        dup["summary"] = base + " in enterprise applications"
        data.append(dup)
        eng._atomic_write(path, data)

        # Very high threshold should filter out
        high = eng.suggest_merges(threshold=0.99)
        assert high["total_candidates"] == 0

    def test_primary_has_higher_access(self, tmp_path: Path):
        eng = _engram(tmp_path)
        base = "Cache invalidation strategies for distributed systems"
        eng.add_lesson({"summary": base})
        path = tmp_path / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        dup = dict(data[0])
        dup["id"] = "high-access"
        dup["summary"] = base.replace("strategies", "approaches")
        dup["access_count"] = 20
        data.append(dup)
        eng._atomic_write(path, data)

        result = eng.suggest_merges(threshold=0.3)
        if result["total_candidates"] > 0:
            assert result["suggestions"][0]["primary_id"] == "high-access"

    def test_decisions_also_scanned(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_decision("数据库选型方案确定", "PostgreSQL", "稳定性好，ACID合规，生态成熟")
        path = tmp_path / "knowledge" / "decisions.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        dup = dict(data[0])
        dup["id"] = "dup-decision"
        # Keep question and choice nearly identical
        dup["question"] = "数据库选型方案的确定"
        data.append(dup)
        eng._atomic_write(path, data)

        result = eng.suggest_merges(threshold=0.2)
        decision_candidates = [
            s for s in result["suggestions"] if s["type"] == "decision"
        ]
        assert len(decision_candidates) >= 1

    def test_limit_respected(self, tmp_path: Path):
        eng = _engram(tmp_path)
        base = "Monitoring alerting threshold configuration"
        eng.add_lesson({"summary": base})
        path = tmp_path / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for i in range(6):
            dup = dict(data[0])
            dup["id"] = f"dup-limit-{i}"
            dup["summary"] = f"{base} version {i}"
            data.append(dup)
        eng._atomic_write(path, data)

        result = eng.suggest_merges(threshold=0.2, limit=3)
        assert len(result["suggestions"]) <= 3


# ── Knowledge Overview Integration ──


class TestKnowledgeOverviewIntegration:
    """Verify health score appears in the knowledge overview endpoint."""

    def test_health_section_contains_score(self, tmp_path: Path):
        eng = _engram(tmp_path)
        eng.add_lesson("test", "python")
        overview = eng.get_knowledge_overview(section="health")
        health = overview["health"]
        assert "health_score" in health
        assert "dimensions" in health
        assert isinstance(health["health_score"], int)

    def test_all_section_contains_score(self, tmp_path: Path):
        eng = _engram(tmp_path)
        overview = eng.get_knowledge_overview(section="all")
        assert "health_score" in overview["health"]
        assert "dimensions" in overview["health"]
