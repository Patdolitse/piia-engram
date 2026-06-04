"""Regression tests for client-validation public evidence docs."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_public_client_evidence_keeps_openclaw_live_unverified():
    text = _read("docs/integrations/client-continuity-evidence.md")

    assert "OpenClaw-compatible static file bridge" in text
    assert "L3" in text
    assert "OpenClaw live agent" in text
    assert "Not verified" in text
    assert "未验证" in text
    assert "Do not claim live OpenClaw agent continuity yet" in text


def test_public_client_evidence_excludes_private_local_paths():
    text = _read("docs/integrations/client-continuity-evidence.md")

    assert "E:\\" not in text
    assert "D:\\" not in text
    assert "raw logs" in text
    assert "stay private" in text


def test_runbook_references_client_validation_harness():
    text = _read("docs/runbooks/agent-client-validation.md")

    assert "piia_engram.client_validation" in text
    assert "scripts/run_client_validation.py" in text
    assert "validate_public_claim" in text
    assert "本地" in text


def test_openclaw_live_plan_is_plan_only():
    text = _read("docs/specs/openclaw-live-agent-plan.md")

    assert "Status: plan only" in text
    assert "L3 static snapshot A/B" in text
    assert "OpenClaw live agent behavior is not yet verified" in text
    assert "Do not claim L4" in text
