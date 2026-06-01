"""Regression tests for the Codex + Claude validation workflow doc."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _doc() -> str:
    return (ROOT / "docs" / "codex-claude-validation.md").read_text(encoding="utf-8")


def test_validation_doc_requires_prompt_files_and_timeout_split():
    text = _doc()

    assert "Use English for Codex-to-Claude prompts" in text
    assert "ASCII-only Markdown prompt files" in text
    assert "claude -p < prompt.md" in text
    assert "If a Claude run times out, do not retry the same broad prompt" in text
    assert "reduce scope" in text


def test_validation_doc_defines_inconclusive_acceptance_state():
    text = _doc()

    assert "VERDICT: INCONCLUSIVE" in text
    assert "timeout/tool limit prevents review" in text
    assert "Treat timeouts as inconclusive, not as PASS" in text


def test_validation_doc_keeps_release_gate_ops_evidence():
    text = _doc()

    assert "sanitize check must return `high=0`" in text
    assert "publish allowlist must pass" in text
    assert "package build and `twine check` must pass" in text
