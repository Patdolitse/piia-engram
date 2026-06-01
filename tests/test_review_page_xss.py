"""XSS / HTML-injection tests for `generate_review_page`.

The page interpolates user-controlled strings (lesson summaries, decision
questions, domain labels, profile fields) into HTML attributes, text nodes,
and ``data-*`` attributes. A naive implementation would let a crafted
summary like ``<script>alert(1)</script>`` execute in the browser.

`generate_review_page` defines a local ``_esc`` helper that escapes
``& < > " '`` and ``\'``. These tests verify that helper is actually applied
to every field that ends up in the HTML.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piia_engram.core import Engram


@pytest.fixture()
def engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_not_raw_in_html(html: str, raw_payload: str, *, label: str) -> None:
    """Fail if the raw (unescaped) payload appears literally in the HTML."""
    assert raw_payload not in html, (
        f"{label}: raw payload found in HTML - XSS escape missing.\n"
        f"  payload: {raw_payload!r}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewPageEscaping:
    def test_lesson_summary_script_tag_escaped(self, engram: Engram):
        payload = "<script>alert('xss')</script>"
        engram.add_lesson({"summary": payload, "domain": "test"})
        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="lesson summary")
        # Escaped form should be present instead
        assert "&lt;script&gt;" in html or "&#x27;xss&#x27;" in html

    def test_lesson_detail_script_tag_escaped(self, engram: Engram):
        payload = "<img src=x onerror=alert(1)>"
        engram.add_lesson(
            {"summary": "safe summary", "detail": payload, "domain": "test"}
        )
        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="lesson detail")

    def test_decision_question_quote_attribute_break_escaped(
        self, engram: Engram
    ):
        """A double-quote in a value embedded in an HTML attribute must be escaped."""
        payload = 'evil"onclick="alert(1)'
        engram.add_decision({"title": payload, "choice": "x"})
        html = engram.generate_review_page(lang="en")
        # The raw payload must not appear — that would close the attribute and inject
        _assert_not_raw_in_html(html, payload, label="decision title")

    def test_domain_label_html_injection_escaped(self, engram: Engram):
        """Domain group labels become HTML; injected tags must be inert."""
        payload = "<b>evil</b>"
        engram.add_lesson({"summary": "lesson body", "domain": payload})
        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="domain label")

    def test_source_tool_in_meta_escaped(self, engram: Engram):
        payload = "<svg/onload=alert(1)>"
        engram.add_lesson(
            {"summary": "lesson body", "domain": "x", "source_tool": payload}
        )
        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="source_tool meta")

    def test_profile_field_escaped(self, engram: Engram):
        """Profile description goes into the identity section — must be escaped."""
        payload = "<iframe src=javascript:alert(1)>"
        engram.update_profile(
            {"role": "engineer", "description": payload}
        )
        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="profile description")

    def test_apostrophe_breaks_attribute_escaped(self, engram: Engram):
        """Apostrophe is in the escape set — verify the single-quote variant works."""
        payload = "evil'onclick='alert(1)"
        engram.add_lesson({"summary": payload, "domain": "test"})
        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="apostrophe break")
        # The escaped form for ' is &#x27;
        assert "&#x27;" in html

    def test_data_id_attribute_escaped(self, engram: Engram):
        """The item-card's ``data-id`` attribute interpolates the item ID.

        IDs are sha256 hex (no special chars), so this is mostly defense in depth,
        but we verify _esc is actually applied to the id field rather than
        relying on the input being clean.
        """
        lesson = engram.add_lesson({"summary": "lesson body", "domain": "x"})
        payload = 'bad" onclick="alert(1)'
        path = engram.root / "knowledge" / "lessons.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in data:
            if item.get("id") == lesson["id"]:
                item["id"] = payload
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        html = engram.generate_review_page(lang="en")
        _assert_not_raw_in_html(html, payload, label="data-id attribute break")
        assert 'data-id="bad&quot; onclick=&quot;alert(1)"' in html

    def test_zh_lang_attribute_renders_correctly(self, engram: Engram):
        """Smoke test: lang=zh produces valid HTML with the correct lang attr."""
        engram.add_lesson({"summary": "测试", "domain": "test"})
        html = engram.generate_review_page(lang="zh")
        assert '<html lang="zh">' in html
        assert "测试" in html  # CJK content should NOT be escaped, only HTML metachars

    def test_ampersand_in_summary_escaped(self, engram: Engram):
        """Bare ``&`` must become ``&amp;`` to keep HTML valid."""
        engram.add_lesson({"summary": "Tom & Jerry", "domain": "test"})
        html = engram.generate_review_page(lang="en")
        # Raw "Tom & Jerry" with a bare & would be malformed HTML
        assert "Tom & Jerry" not in html
        assert "Tom &amp; Jerry" in html

    def test_extraction_quality_metadata_rendered_and_escaped(self, engram: Engram):
        """Quality metadata is user-controlled storage and must be escaped."""
        method_payload = 'notes" onclick="alert(1)'
        signal_payload = "<img src=x onerror=alert(1)>"
        evidence_payload = "<script>alert('evidence')</script>"
        engram.add_lesson({
            "summary": "quality item",
            "tier": "staging",
            "extraction": {
                "method": method_payload,
                "quality_score": 0.88,
                "quality_signals": ["durable_rule", signal_payload],
                "quality_flags": ["reviewable"],
                "evidence_span": evidence_payload,
            },
        })

        html = engram.generate_review_page(lang="en")

        assert "q=0.88" in html
        assert "durable_rule" in html
        _assert_not_raw_in_html(html, method_payload, label="quality method")
        _assert_not_raw_in_html(html, signal_payload, label="quality signal")
        _assert_not_raw_in_html(html, evidence_payload, label="quality evidence")
        assert "&lt;img" in html
        assert "&lt;script&gt;" in html
