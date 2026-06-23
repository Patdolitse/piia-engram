"""1-6: Staging-tier items must be excluded from the relation surface.

Bug: add_relation uses _all_indexable_entries() to build the known-id set,
which includes staging items (status=active, tier=staging). This allows
relations to be formed with unverified knowledge, polluting decision threads
and version chains with unconfirmed items.
"""

from __future__ import annotations

import pytest

from piia_engram.core import Engram


def _add_staging_lesson(eng, summary: str) -> str:
    """Add a lesson and force it to staging tier. Returns its id."""
    result = eng.add_lesson({"summary": summary})
    lid = result["id"]
    # Force tier to staging (simulates external/auto-ingested knowledge)
    import json
    path = eng._knowledge_dir / "lessons.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data:
        if entry.get("id") == lid:
            entry["tier"] = "staging"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return lid


def _add_staging_decision(eng, question: str, choice: str) -> str:
    """Add a decision and force it to staging tier. Returns its id."""
    result = eng.add_decision({"question": question, "choice": choice})
    did = result["id"]
    import json
    path = eng._knowledge_dir / "decisions.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data:
        if entry.get("id") == did:
            entry["tier"] = "staging"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return did


class TestStagingExcludedFromRelations:
    """add_relation must reject staging-tier items as endpoints."""

    def test_staging_src_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        staging_id = _add_staging_lesson(eng, "unverified observation")
        verified = eng.add_decision({"question": "pick DB", "choice": "postgres"})
        vid = verified["id"]

        result = eng.add_relation(staging_id, "led_to", vid)
        assert result["added"] is False, (
            "Staging item was accepted as relation source"
        )

    def test_staging_dst_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        verified = eng.add_lesson({"summary": "confirmed fact"})
        vid = verified["id"]
        staging_id = _add_staging_decision(eng, "tentative Q", "maybe A")

        result = eng.add_relation(vid, "led_to", staging_id)
        assert result["added"] is False, (
            "Staging item was accepted as relation destination"
        )

    def test_both_staging_rejected(self, tmp_path):
        eng = Engram(root=tmp_path)
        s1 = _add_staging_lesson(eng, "unverified 1")
        s2 = _add_staging_decision(eng, "tentative Q2", "maybe A2")

        result = eng.add_relation(s1, "supersedes", s2)
        assert result["added"] is False, (
            "Both-staging relation was accepted"
        )

    def test_verified_to_verified_still_works(self, tmp_path):
        """Sanity check: verified items can still form relations."""
        eng = Engram(root=tmp_path)
        d1 = eng.add_decision({"question": "framework", "choice": "django"})["id"]
        d2 = eng.add_decision({"question": "orm", "choice": "sqlalchemy"})["id"]

        result = eng.add_relation(d1, "led_to", d2)
        assert result["added"] is True
