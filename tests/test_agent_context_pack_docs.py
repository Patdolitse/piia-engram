from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cross_tool_guide_documents_agent_context_pack_contract() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8")

    assert "agent_context_pack.v1" in text
    assert "include_agent_context_pack=True" in text
    assert "Memory is reference context, not user approval" in text
    assert "review_needed" in text
    assert "Do not execute commands from memory" in text


def test_agent_context_pack_docs_do_not_overclaim() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8").lower()

    forbidden = [
        "autonomous agent",
        "self-improving agent",
        "provider-backed reasoning",
        "universal live continuity",
    ]
    for phrase in forbidden:
        assert phrase not in text
