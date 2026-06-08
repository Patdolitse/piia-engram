"""Risk-based write gate (N3 cold-start -> approve -> resume convergence).

Policy under test (chosen by the owner): the memory write gate is *risk-based*,
not *source-based*. Low- and medium-risk knowledge auto-absorbs straight to
``verified`` (with a post-hoc audit entry); only high-risk knowledge
(credentials / shell commands / MCP config / permission rules) is held in
``staging`` for explicit owner approval. An explicit caller-supplied ``tier``
is honored (deliberate seeds / fixtures), and an already-rejected/deprecated
entry is never silently promoted.

These tests pin that contract so a future refactor cannot quietly revert to
"everything auto-extracted is staging" (empty-store friction) or to
"everything add_lesson is verified" (high-risk content bypassing review).
"""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram.core import Engram


def _engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _audit_details(engram: Engram) -> list[str]:
    path = getattr(engram._audit, "log_path", None)
    if not path or not Path(path).exists():
        return []
    out: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line).get("detail", ""))
        except Exception:
            continue
    return out


# --------------------------------------------------------------------------
# Lessons
# --------------------------------------------------------------------------

def test_low_risk_lesson_auto_absorbs_to_verified(tmp_path: Path):
    engram = _engram(tmp_path)
    lesson = engram.add_lesson(
        {"summary": "prefer small pure functions for testability", "domain": "python"}
    )
    assert lesson["risk_level"] == "low"
    assert lesson["tier"] == "verified"
    assert lesson["memory_state"] == "verified"
    assert lesson["approval_status"] == "approved"
    assert lesson["approval_required"] is False


def test_medium_risk_lesson_auto_absorbs_to_verified(tmp_path: Path):
    engram = _engram(tmp_path)
    lesson = engram.add_lesson(
        {"summary": "good reference write-up at https://example.com/post", "domain": "research"}
    )
    assert lesson["risk_level"] == "medium"
    assert "external_url" in lesson["risk_flags"]
    assert lesson["tier"] == "verified"
    assert lesson["approval_status"] == "approved"


def test_high_risk_lesson_is_gated_to_staging(tmp_path: Path):
    engram = _engram(tmp_path)
    lesson = engram.add_lesson(
        {"summary": "rotate the api_key and run command to redeploy", "domain": "ops"}
    )
    assert lesson["risk_level"] == "high"
    assert lesson["tier"] == "staging"
    assert lesson["memory_state"] == "staging"
    assert lesson["approval_status"] == "pending"
    assert lesson["approval_required"] is True


def test_explicit_tier_is_honored_over_risk_gate(tmp_path: Path):
    engram = _engram(tmp_path)
    # Low-risk content, but caller explicitly pins staging -> respected.
    lesson = engram.add_lesson(
        {"summary": "a perfectly benign note", "domain": "misc", "tier": "staging"}
    )
    assert lesson["risk_level"] == "low"
    assert lesson["tier"] == "staging"


def test_rejected_lesson_is_not_promoted_by_gate(tmp_path: Path):
    engram = _engram(tmp_path)
    lesson = engram.add_lesson(
        {"summary": "a rejected low-risk draft", "status": "rejected"}
    )
    # Gate must preserve the rejected state, not auto-absorb to verified.
    assert lesson["memory_state"] == "rejected"
    assert lesson["approval_status"] == "rejected"


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------

def test_low_risk_decision_auto_absorbs_to_verified(tmp_path: Path):
    engram = _engram(tmp_path)
    decision = engram.add_decision(
        {"question": "Which db for local index?", "choice": "SQLite", "domain": "arch"}
    )
    assert decision["risk_level"] == "low"
    assert decision["tier"] == "verified"
    assert decision["approval_status"] == "approved"


def test_high_risk_decision_is_gated_to_staging(tmp_path: Path):
    engram = _engram(tmp_path)
    # Value-bearing content (real credentials) -> genuinely high risk. Prose
    # alone ("bypass the approval allowlist") is a weak signal and now maps to
    # medium under value-match-priority classification.
    decision = engram.add_decision(
        {
            "question": "Where should the deploy bot read its server_key?",
            "choice": "store the api_key and password in the mcp_server config",
            "domain": "security",
        }
    )
    assert decision["risk_level"] == "high"
    assert decision["tier"] == "staging"
    assert decision["approval_status"] == "pending"


# --------------------------------------------------------------------------
# Audit moat: every write logs the gate decision
# --------------------------------------------------------------------------

def test_gate_decision_is_audited(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENGRAM_AUDIT", "1")
    engram = _engram(tmp_path)
    engram.add_lesson({"summary": "a low risk note for audit", "domain": "misc"})
    engram.add_lesson({"summary": "rotate api_key via run command now", "domain": "ops"})
    details = _audit_details(engram)
    assert any("auto-absorbed->verified" in d for d in details)
    assert any("gated->staging" in d for d in details)


# --------------------------------------------------------------------------
# Cold-start visibility: pending-review surfaced in resume brief
# --------------------------------------------------------------------------

def test_resume_brief_surfaces_pending_review_count(tmp_path: Path):
    engram = _engram(tmp_path)
    # One high-risk write lands in staging -> should show up as pending review.
    engram.add_lesson({"summary": "rotate api_key with run command", "domain": "ops"})
    engram.add_lesson({"summary": "a benign low-risk note", "domain": "misc"})

    brief = engram.get_resume_brief()
    markdown = brief["markdown"]
    assert "pending_review" in markdown
    assert "高风险" in markdown


def test_resume_brief_no_pending_line_when_store_clean(tmp_path: Path):
    engram = _engram(tmp_path)
    engram.add_lesson({"summary": "only a benign low-risk note", "domain": "misc"})
    brief = engram.get_resume_brief()
    assert "pending_review" not in brief["markdown"]
