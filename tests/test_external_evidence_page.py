"""External evidence page local draft tests."""

from __future__ import annotations

from piia_engram import external_evidence_page as eep


def test_external_evidence_page_is_local_draft_and_metadata_only():
    page = eep.render_external_evidence_draft(
        [
            {
                "label": "PyPI",
                "url": "https://pypi.org/project/piia-engram/3.51.2/",
                "status": "verified",
                "checked_at": "2026-06-06T00:00:00Z",
                "private_note": "SECRET local token",
            }
        ],
        title="Engram evidence",
    )

    assert "Engram evidence" in page
    assert "LOCAL DRAFT" in page
    assert "requires owner confirmation before publishing" in page
    assert "https://pypi.org/project/piia-engram/3.51.2/" in page
    assert "SECRET local token" not in page


def test_external_evidence_page_rejects_unsupported_status():
    page = eep.render_external_evidence_draft(
        [{"label": "Unknown", "url": "https://example.com", "status": "done"}]
    )

    assert "status=unknown" in page
    assert "done" not in page
