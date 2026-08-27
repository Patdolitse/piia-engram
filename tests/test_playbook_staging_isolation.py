"""Playbook staging-isolation regressions (PR-4).

Covers the Codex-flagged lifecycle gap: newer staged playbooks must never
consume the recency slots that older verified playbooks should fill, and
staged playbooks must stay invisible in cold-start surfaces while remaining
reachable in the review surface.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _eng(tmp_path: Path):
    """Use the conftest-isolated store root (don't fight the autouse fixture)."""
    from piia_engram.core import Engram

    return Engram()


def _pb(eng, title: str, tier: str, reviewed: str) -> dict:
    return eng.add_playbook({
        "title": title,
        "triggers": [f"trigger-{title}"],
        "steps": [{"order": 1, "action": f"step for {title}", "detail": ""}],
        "domain": "testing",
        "tier": tier,
    })


class TestPlaybookStagingIsolation:
    def test_staged_newer_does_not_occupy_verified_slots(self, tmp_path, monkeypatch):
        """Multiple newer staging playbooks fill the limit; an older verified
        playbook must still be returned (filter-before-sort-before-limit)."""
        eng = _eng(tmp_path)
        verified = _pb(eng, "Old verified playbook", "verified", "2026-01-01")
        verified_title = verified.get("title", "")
        for i in range(7):
            _pb(eng, f"New staged playbook {i}", "staging", f"2026-08-{i+1:02d}")

        recent = eng.get_recent_playbooks(limit=3)
        assert verified_title in [r.get("title", "") for r in recent], (
            "older verified playbook must appear despite newer staging entries"
        )
        assert all(r.get("tier") == "verified" for r in recent)

    def test_staged_not_in_cold_start_context(self, tmp_path):
        """The cold-start surface is get_recent_playbooks (the code path that
        generate_context's playbook section calls); test it directly to
        avoid reconcile env pollution on dev machines."""
        eng = _eng(tmp_path)
        _pb(eng, "SecretStagedPlaybook", "staging", "2026-08-01")
        _pb(eng, "PublicVerifiedGuide", "verified", "2026-08-01")

        recent = eng.get_recent_playbooks(limit=5)
        titles = [r.get("title", "") for r in recent]
        assert "PublicVerifiedGuide" in titles
        assert "SecretStagedPlaybook" not in titles

    def test_staged_reachable_in_review_surface(self, tmp_path, monkeypatch):
        """Staged playbooks stay visible through the management view (the
        review surface), not just the cold-start context."""
        eng = _eng(tmp_path)
        _pb(eng, "STAGED-BUT-REVIEWABLE", "staging", "2026-08-01")

        # the review surface is the MCP tool layer; at core level, staged
        # playbooks are retrievable through the index (unlike cold-start)
        all_pbs = eng._export_playbooks()
        titles = [p.get("title", "") for p in all_pbs]
        assert any("STAGED-BUT-REVIEWABLE" in t for t in titles), (
            "staged playbook must be reachable in the review surface"
        )
