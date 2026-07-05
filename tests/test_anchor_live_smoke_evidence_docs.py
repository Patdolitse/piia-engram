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


def test_anchor_live_smoke_doc_includes_reply_rendering_boundary() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "collect_anchor_live_smoke_evidence.py --json --live --allow-live" in text
    assert "render_anchor_forum_reply.py" in text
    assert "Owner confirmation required before posting" in text
    assert "Do not post raw memory bodies" in text


def test_anchor_live_smoke_doc_includes_packet_finalizer() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "validate_anchor_live_smoke_evidence.py" in text
    assert "build_anchor_forum_evidence_packet.py" in text
    assert "--live --allow-live" in text
    assert "anchor-live-smoke-metrics.md" in text
    assert "manifest.json" in text
    assert "Accepted aggregate input shape" in text
    assert "No public forum reply is sent" in text


def test_anchor_live_smoke_doc_includes_continuous_history_workflow() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "append_anchor_live_smoke_history.py --live --allow-live" in text
    assert ".engram-local-evidence/anchor-live-smoke-history/anchor-live-smoke-history.jsonl" in text
    assert ".engram-local-evidence/anchor-live-smoke-history/latest.json" in text
    assert ".engram-local-evidence/anchor-live-smoke-history/summary.md" in text
    assert "--history-summary .engram-local-evidence/anchor-live-smoke-history/latest.json" in text
    assert "current live store has no structured anchor records" in text
