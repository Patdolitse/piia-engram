from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.staging_review import batch_review_staging, list_pending_staging


@pytest.fixture
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    return Engram(root=tmp_path)


def test_strict_low_risk_lesson_goes_to_staging(
    eng: Engram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    lesson = eng.add_lesson("strict mode low risk lesson", domain="approval")

    assert lesson["tier"] == "staging"
    assert lesson["memory_state"] == "staging"
    assert lesson["approval_status"] == "pending"
    assert lesson["approval_required"] is True


def test_strict_low_risk_decision_goes_to_staging(
    eng: Engram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    decision = eng.add_decision(
        "strict mode decision?", "stage first", "strict approval is enabled"
    )

    assert decision["tier"] == "staging"
    assert decision["memory_state"] == "staging"
    assert decision["approval_status"] == "pending"
    assert decision["approval_required"] is True


def test_strict_gates_explicit_tier_but_preserves_rejected_state(
    eng: Engram, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Strict mode is an un-bypassable owner gate: even a caller-pinned
    # tier="verified" (the kind a caller could smuggle through content_json)
    # must be sent to staging. A deliberately rejected state is still
    # preserved (negative states are never bumped, even into staging).
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")

    explicit = eng.add_lesson(
        {"summary": "explicit verified fixture", "tier": "verified"}
    )
    rejected = eng.add_lesson(
        {"summary": "rejected draft stays rejected", "status": "rejected"}
    )

    assert explicit["tier"] == "staging"
    assert explicit["memory_state"] == "staging"
    assert explicit["approval_status"] == "pending"
    assert explicit["approval_required"] is True
    assert rejected["status"] == "rejected"
    assert rejected["memory_state"] == "rejected"
    assert rejected["approval_status"] == "rejected"


def test_default_mode_still_honors_explicit_tier(eng: Engram) -> None:
    # Outside strict mode, a deliberately caller-pinned tier remains an
    # escape hatch for seeds / imports / fixtures.
    explicit = eng.add_lesson(
        {"summary": "explicit verified seed outside strict", "tier": "verified"}
    )

    assert explicit["tier"] == "verified"
    assert explicit["memory_state"] == "verified"


def test_default_mode_keeps_risk_based_gate(eng: Engram) -> None:
    low = eng.add_lesson("default mode low risk lesson", domain="approval")
    high = eng.add_lesson(
        {
            "summary": "store this api_key=sk-test-value for a service",
            "domain": "security",
        }
    )

    assert low["tier"] == "verified"
    assert low["memory_state"] == "verified"
    assert high["tier"] == "staging"
    assert high["memory_state"] == "staging"


def test_strict_pending_item_can_be_approved(
    eng: Engram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGRAM_APPROVAL", "strict")
    lesson = eng.add_lesson("strict pending approval flow", domain="approval")

    pending = list_pending_staging(eng)
    assert pending["counts"]["total_pending"] == 1
    assert pending["items"][0]["id"] == lesson["id"]

    applied = batch_review_staging(
        eng,
        [{"id": lesson["id"], "action": "approve"}],
        dry_run=False,
        confirm=True,
    )

    assert applied["status"] == "applied"
    assert applied["counts"]["applied"] == 1
    stored = eng.get_lessons(limit=None, _update_access=False)[0]
    assert stored["tier"] == "verified"
    assert stored["memory_state"] == "verified"


def test_strict_env_parsing_is_trimmed_case_insensitive_and_opt_in(
    eng: Engram, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENGRAM_APPROVAL", " Strict ")
    strict_lesson = eng.add_lesson("strict env spelling lesson", domain="approval")

    monkeypatch.setenv("ENGRAM_APPROVAL", "loose")
    loose_lesson = eng.add_lesson("loose env spelling lesson", domain="approval")

    monkeypatch.setenv("ENGRAM_APPROVAL", "")
    empty_lesson = eng.add_lesson("empty env spelling lesson", domain="approval")

    assert strict_lesson["tier"] == "staging"
    assert loose_lesson["tier"] == "verified"
    assert empty_lesson["tier"] == "verified"
