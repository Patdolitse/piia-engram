"""P0 regression: an agent must not smuggle a trust tier through the MCP write
boundary.

The risk-based write gate is the sole authority over ``tier``: low/medium-risk
content auto-absorbs to ``verified``; high-risk content (credentials / shell /
MCP config / permission rules) is held in ``staging`` for explicit owner
approval. An *agent-facing* MCP entry (``memory_store`` / ``bulk_add_knowledge``)
accepts a free-form JSON payload, so a caller could try to pre-set
``tier="verified"`` and short-circuit the staging gate via core's
``tier_explicit`` escape hatch.

These tests pin the fix: the MCP boundary strips
:data:`piia_engram.storage.UNTRUSTED_TRUST_FIELDS` from every payload before it
reaches the gate, so smuggled trust fields are ignored and the gate decides.
The escape hatch is preserved for *internal* callers (seeds / imports /
fixtures) that call ``Engram.add_lesson`` directly — that path is intentionally
NOT routed through the strip, and is covered here too so a future refactor
can't quietly close the legitimate seam.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram import mcp_server
from piia_engram.core import Engram, strip_untrusted_trust_fields
from piia_engram.storage import UNTRUSTED_TRUST_FIELDS


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    """Isolated Engram instance patched into mcp_server._engram."""
    monkeypatch.delenv("ENGRAM_APPROVAL", raising=False)
    engram = Engram(root=tmp_path)
    monkeypatch.setattr(mcp_server, "_engram", engram)
    return engram


def _run(coro):
    return asyncio.run(coro)


# A high-risk lesson: a literal credential value -> classified high -> staging.
_HIGH_RISK_LESSON = {
    "summary": "store this api_key=sk-test-value for the deploy bot",
    "domain": "security",
}
# A high-risk decision: real credentials in the choice -> staging.
_HIGH_RISK_DECISION = {
    "question": "Where should the deploy bot read its server_key?",
    "choice": "store the api_key and password in the mcp_server config",
    "domain": "security",
}
# Every trust/approval field a caller might try to smuggle, all set to the
# most-trusted value so a successful smuggle would be unmistakable.
_SMUGGLE = {
    "tier": "verified",
    "memory_state": "verified",
    "approval_status": "approved",
    "approval_required": False,
}


# ---------------------------------------------------------------------------
# memory_store
# ---------------------------------------------------------------------------


def test_memory_store_lesson_strips_smuggled_tier_to_staging(eng: Engram) -> None:
    content = {**_HIGH_RISK_LESSON, **_SMUGGLE}
    out = _run(mcp_server.memory_store("lesson", json.dumps(content)))
    assert "失败" not in out  # write succeeded

    stored = eng.get_lessons(limit=None, _update_access=False)
    assert len(stored) == 1
    item = stored[0]
    assert item["risk_level"] == "high"
    # The gate, not the payload, decided the tier.
    assert item["tier"] == "staging"
    assert item["memory_state"] == "staging"
    assert item["approval_status"] == "pending"
    assert item["approval_required"] is True


def test_memory_store_decision_strips_smuggled_tier_to_staging(eng: Engram) -> None:
    content = {**_HIGH_RISK_DECISION, **_SMUGGLE}
    out = _run(mcp_server.memory_store("decision", json.dumps(content)))
    assert "失败" not in out

    stored = eng.get_decisions(limit=None, _update_access=False)
    assert len(stored) == 1
    item = stored[0]
    assert item["risk_level"] == "high"
    assert item["tier"] == "staging"
    assert item["memory_state"] == "staging"
    assert item["approval_status"] == "pending"
    assert item["approval_required"] is True


def test_memory_store_smuggled_staging_on_low_risk_still_verifies(eng: Engram) -> None:
    # Smuggling works in BOTH directions: a caller can't force ``staging`` on
    # benign content either. After the strip, low-risk content auto-absorbs to
    # verified — proving the field is genuinely ignored, not clamped one way.
    content = {
        "summary": "prefer small pure functions for testability",
        "domain": "python",
        "tier": "staging",
        "memory_state": "staging",
    }
    out = _run(mcp_server.memory_store("lesson", json.dumps(content)))
    assert "失败" not in out

    item = eng.get_lessons(limit=None, _update_access=False)[0]
    assert item["risk_level"] == "low"
    assert item["tier"] == "verified"
    assert item["memory_state"] == "verified"


# ---------------------------------------------------------------------------
# bulk_add_knowledge
# ---------------------------------------------------------------------------


def test_bulk_add_knowledge_strips_smuggled_tier_each_item(eng: Engram) -> None:
    items = [
        {
            "summary": "rotate the api_key and run command to redeploy svc-a",
            "domain": "ops",
            **_SMUGGLE,
        },
        {
            "summary": "the db password is stored in the mcp_server config for svc-b",
            "domain": "security",
            **_SMUGGLE,
        },
    ]
    out = _run(mcp_server.bulk_add_knowledge(json.dumps(items), item_type="lesson"))
    report = json.loads(out)
    assert report["saved"] == 2

    stored = eng.get_lessons(limit=None, _update_access=False)
    assert len(stored) == 2
    for item in stored:
        assert item["risk_level"] == "high"
        assert item["tier"] == "staging"
        assert item["memory_state"] == "staging"
        assert item["approval_status"] == "pending"


# ---------------------------------------------------------------------------
# Escape hatch preserved for internal callers (seeds / imports / fixtures)
# ---------------------------------------------------------------------------


def test_internal_core_caller_keeps_tier_escape_hatch(eng: Engram) -> None:
    # The strip lives at the MCP boundary ONLY. A direct core call (a seed, an
    # import, a fixture) may still pin a tier on high-risk content — otherwise
    # legitimate verified seeds would be impossible. This guards against a
    # refactor that pushes the strip down into core and breaks that seam.
    seeded = eng.add_lesson({**_HIGH_RISK_LESSON, "tier": "verified"})
    assert seeded["risk_level"] == "high"
    assert seeded["tier"] == "verified"
    assert seeded["memory_state"] == "verified"


# ---------------------------------------------------------------------------
# Unit: the shared helper
# ---------------------------------------------------------------------------


def test_strip_helper_removes_all_untrusted_fields() -> None:
    payload = {
        "summary": "keep me",
        "tier": "verified",
        "memory_state": "verified",
        "approval_status": "approved",
        "approval_required": False,
    }
    returned = strip_untrusted_trust_fields(payload)
    assert returned is payload  # mutates in place and returns the same object
    assert payload == {"summary": "keep me"}
    for field in UNTRUSTED_TRUST_FIELDS:
        assert field not in payload


def test_strip_helper_is_noop_on_non_dict() -> None:
    for value in ([1, 2, 3], "tier=verified", None, 42):
        assert strip_untrusted_trust_fields(value) is value


def test_untrusted_trust_fields_contract() -> None:
    # Pin the exact field set so a silent narrowing (e.g. dropping
    # ``approval_required``) can't reopen a smuggle vector unnoticed.
    assert set(UNTRUSTED_TRUST_FIELDS) == {
        "tier",
        "memory_state",
        "approval_status",
        "approval_required",
    }
