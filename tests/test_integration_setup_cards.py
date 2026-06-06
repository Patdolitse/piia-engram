"""Static guards for client setup cards and operator onboarding."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


CLIENT_CARDS = [
    "docs/integrations/claude-code.md",
    "docs/integrations/codex.md",
    "docs/integrations/cursor.md",
]


def test_client_setup_cards_are_public_safe_and_evidence_bounded():
    for path in CLIENT_CARDS:
        text = _read(path)
        assert "python -m piia_engram.mcp_server" in text
        assert "ENGRAM_TOOLS=all" in text
        assert "Smoke test" in text
        assert "L2" in text
        assert "L4" in text
        assert "operator MCP cheatsheet" in text or "operator-mcp-cheatsheet.md" in text
        assert "E:\\" not in text
        assert "D:\\" not in text
        assert "works with every AI tool" not in text


def test_new_onboarding_docs_are_publish_allowlisted():
    allowlist = _read(".publishallow")

    for path in [*CLIENT_CARDS, "docs/operator-mcp-cheatsheet.md"]:
        assert path in allowlist


def test_first_value_quickstart_links_setup_cards_and_troubleshooting():
    text = _read("docs/quickstart-first-value.md")

    for phrase in [
        "integrations/claude-code.md",
        "integrations/codex.md",
        "integrations/cursor.md",
        "operator-mcp-cheatsheet.md",
        "If recall did not fire",
        "L2 read/search capable",
    ]:
        assert phrase in text
