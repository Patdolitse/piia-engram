"""Source-aware freshness analytics read-path tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from piia_engram.core import Engram


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    return Engram(root=tmp_path)


def _old_iso(days: int = 200) -> str:
    return (datetime.now() - timedelta(days=days)).replace(microsecond=0).isoformat()


def _ids(rows: list[dict]) -> set[str]:
    return {str(row.get("id")) for row in rows}


def test_analytics_read_paths_honor_skip_decay(eng: Engram) -> None:
    old = _old_iso()
    skip_item = eng.add_lesson(
        {
            "summary": "test signal backed fact should not age into stale lane",
            "domain": "freshness",
            "timestamp": old,
            "created_at": old,
            "last_reviewed": old,
            "access_count": 3,
            "provenance": {
                "source_agent": "owner",
                "confirmation_source": "test_signal",
                "last_validated_at": old,
            },
        },
        _allow_internal_provenance=True,
    )
    review_item = eng.add_lesson(
        {
            "summary": "ordinary stale fact that needs review",
            "domain": "freshness",
            "timestamp": old,
            "created_at": old,
            "last_reviewed": old,
            "access_count": 3,
        }
    )
    archive_item = eng.add_lesson(
        {
            "summary": "ordinary stale fact that can be archived",
            "domain": "freshness",
            "timestamp": old,
            "created_at": old,
            "last_reviewed": old,
            "access_count": 0,
        }
    )

    stale = eng.get_stale_knowledge(days=30, limit=None)
    stale_ids = _ids(stale["lessons"] + stale["decisions"])

    assert skip_item["id"] not in stale_ids
    assert review_item["id"] in stale_ids
    assert archive_item["id"] in stale_ids

    health = eng.get_health_report()
    review_ids = _ids(health["items_needing_review"])
    archive_ids = _ids(health["items_to_archive"])

    assert health["dimensions"]["freshness"] == 33
    assert skip_item["id"] not in review_ids
    assert skip_item["id"] not in archive_ids
    assert review_item["id"] in review_ids
    assert archive_item["id"] in archive_ids


def test_stale_knowledge_decisions_loop_honors_skip_decay(eng: Engram) -> None:
    """The decisions loop (not just lessons) must skip trigger-managed items."""
    old = _old_iso()
    skip_decision = eng.add_decision(
        {
            "question": "which test runner for the suite",
            "choice": "vitest, backed by a re-runnable test signal",
            "domain": "freshness",
            "timestamp": old,
            "created_at": old,
            "last_reviewed": old,
            "access_count": 3,
            "provenance": {
                "source_agent": "owner",
                "confirmation_source": "test_signal",
                "last_validated_at": old,
            },
        },
        _allow_internal_provenance=True,
    )
    ordinary_decision = eng.add_decision(
        {
            "question": "which database for the service",
            "choice": "postgres",
            "domain": "freshness",
            "timestamp": old,
            "created_at": old,
            "last_reviewed": old,
            "access_count": 3,
        }
    )

    stale = eng.get_stale_knowledge(days=30, limit=None)
    stale_ids = _ids(stale["lessons"] + stale["decisions"])

    assert skip_decision["id"] not in stale_ids
    assert ordinary_decision["id"] in stale_ids
