from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "anchor-live-smoke-weekend-evidence.md"


def test_anchor_live_smoke_evidence_packet_defines_public_boundary() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Do not post raw memory bodies" in text
    assert "Any forum response still needs owner confirmation before posting" in text
    assert "aggregate counts" in text


def test_anchor_live_smoke_evidence_packet_tracks_weekend_merge_inputs() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "Anchor results: checked, valid, invalid, unknown, superseded" in text
    assert "LIVE_SMOKE results: runs, passed, failed" in text
    assert "tests/test_mcp_entrypoint_smoke.py" in text
    assert "Project-level Claude folder checked" in text
