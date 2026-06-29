from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cross_tool_guide_documents_lightweight_wrap_up_boundary() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8")

    assert "wrap_up_session" in text
    assert "lightweight session-end save" in text
    assert "does not run full reconciliation by default" in text
    assert "run_reconcile=True" in text


def test_wrap_up_docs_do_not_claim_background_autonomy() -> None:
    text = (ROOT / "docs" / "cross-tool-guide.md").read_text(encoding="utf-8").lower()

    forbidden = [
        "autonomous background sync",
        "always-on reconciliation",
        "guaranteed live sync",
        "provider-backed cleanup",
    ]
    for phrase in forbidden:
        assert phrase not in text
