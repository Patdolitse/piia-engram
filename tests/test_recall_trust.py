"""M3: owner-only trust surface in the recall projection.

The recall projection deliberately hides internal trust bookkeeping. For the
onboard-repo first-value story the OWNER needs to see, per fact, why it's
trustworthy / its anchor / when it expires / when it was validated. This is an
allowlisted `trust` block, gated owner-only (include_trust); non-owner /
safe-context recall stays governed and never sees it.
"""
from __future__ import annotations

from piia_engram import recall


def _verified_anchor_entry() -> dict:
    return {
        "id": "abc123def456",
        "summary": "This project depends on `react` (^18.2.0).",
        "domain": "repo-fact",
        "tier": "verified",
        "provenance": {
            "confirmation_source": "anchor",
            "anchor_ref": "dep:react",
            "anchor_status": "valid",
            "anchor_project_id": "github.com/acme/app",
            "source_agent": "owner",
            "last_validated_at": "2026-06-18T10:00:00",
        },
    }


def test_owner_trust_block_present_when_included():
    view = recall._project_item(
        _verified_anchor_entry(), include_freshness=True, now=None, include_trust=True
    )
    trust = view["trust"]
    assert trust["confirmation_source"] == "anchor"        # why-trustworthy
    assert trust["anchor"] == "dep:react"                  # anchor
    assert trust["anchor_status"] == "valid"
    assert trust["anchor_project_id"] == "github.com/acme/app"
    assert trust["validated_at"] == "2026-06-18T10:00:00"  # validated-at
    assert "decay_policy" in trust                          # expires (derived, honest)


def test_no_trust_block_by_default_governed():
    view = recall._project_item(
        _verified_anchor_entry(), include_freshness=True, now=None
    )
    assert "trust" not in view                              # non-owner stays governed


def test_trust_block_omits_missing_anchor_fields():
    entry = {
        "id": "x", "summary": "s", "tier": "verified",
        "provenance": {"confirmation_source": "human", "source_agent": "owner"},
    }
    view = recall._project_item(entry, include_freshness=True, now=None, include_trust=True)
    trust = view["trust"]
    assert trust["confirmation_source"] == "human"
    assert "anchor" not in trust          # no anchor_ref on this fact
    assert "anchor_status" not in trust


def test_build_recall_payload_surfaces_trust_when_included():
    payload = recall.build_recall_payload(
        relevant_knowledge=[_verified_anchor_entry()], include_trust=True
    )
    item = payload["knowledge"][0]
    assert item["trust"]["confirmation_source"] == "anchor"
    assert item["trust"]["anchor"] == "dep:react"


def test_build_recall_payload_no_trust_by_default():
    payload = recall.build_recall_payload(relevant_knowledge=[_verified_anchor_entry()])
    assert "trust" not in payload["knowledge"][0]
