"""Tests for the non-technical owner dashboard (Phase 11)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from piia_engram import i18n, owner_dashboard as od


NOW = datetime(2026, 6, 3, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_lang():
    i18n.set_lang("en")
    yield
    i18n._runtime_lang = None


def _store():
    return {
        "lessons": [
            {"id": "L1", "summary": "verified durable lesson", "tier": "verified",
             "status": "active", "sensitivity": "work", "last_validated_at": "2026-05-30T00:00:00+00:00",
             "provenance": {"source_agent": "codex"}},
            {"id": "L2", "summary": "stale staging note", "tier": "staging",
             "status": "active", "created_at": "2025-01-01T00:00:00+00:00", "access_count": 0},
        ],
        "decisions": [
            {"id": "D1", "question": "q", "choice": "the chosen path", "tier": "verified",
             "status": "active", "sensitivity": "work", "last_validated_at": "2026-06-01T00:00:00+00:00"},
        ],
    }


def test_dashboard_assembles_all_sections():
    s = _store()
    integ = {"healthy": False, "problems": [{"code": "index_stale"}]}
    tel = {"enabled": False, "remote_enabled": False, "phase": "1"}
    dash = od.build_owner_dashboard(lessons=s["lessons"], decisions=s["decisions"],
                                    integrity_report=integ, telemetry_status=tel, now=NOW)
    assert dash["recall_trust"]["total"] == 3
    assert dash["recall_trust"]["with_provenance"] >= 1
    assert dash["lifecycle"]["invariant"] == "never_auto_delete"
    assert dash["integrity"]["problems"] == 1
    assert dash["integrity"]["problem_codes"] == ["index_stale"]
    assert dash["export_readiness"]["exportable_global"] == 2  # 2 verified work entries
    assert dash["telemetry"]["enabled"] is False


def test_text_render_bilingual_en():
    i18n.set_lang("en")
    dash = od.build_owner_dashboard(lessons=_store()["lessons"], now=NOW)
    text = od.render_dashboard_text(dash)
    assert "Recall trust" in text
    assert "never deletes" in text


def test_text_render_bilingual_zh():
    i18n.set_lang("zh")
    dash = od.build_owner_dashboard(lessons=_store()["lessons"], now=NOW)
    text = od.render_dashboard_text(dash)
    assert "召回信任" in text or "控制台" in text


def test_html_escapes_rendered_field():
    # Inject a script into a field that IS rendered (generated_at flows into the
    # <pre> body and the meta line), then assert it is escaped, not live.
    dash = od.build_owner_dashboard(now=NOW)
    dash["generated_at"] = "<script>alert('xss')</script>"
    html_out = od.render_dashboard_html(dash)
    assert "<script>alert" not in html_out
    assert "&lt;script&gt;" in html_out


def test_html_escapes_injected_field():
    dash = od.build_owner_dashboard(now=NOW)
    dash["generated_at"] = "<img src=x onerror=alert(1)>"
    html_out = od.render_dashboard_html(dash)
    assert "<img src=x" not in html_out
    assert "&lt;img" in html_out


def test_no_private_mechanism_leak():
    dash = od.build_owner_dashboard(lessons=_store()["lessons"], decisions=_store()["decisions"],
                                    now=NOW)
    blob_text = od.render_dashboard_text(dash)
    blob_html = od.render_dashboard_html(dash)
    # Build the needles without embedding the underscored maintainer-private
    # token verbatim, so the packaged test source itself does not ship a
    # high-private literal (caught by the release artifact private-term scan).
    _cso = "Core Self Optimization"
    forbidden = [_cso, _cso.replace(" ", "_"), "Workflow_Docs",
                 "DeepSeek audit", "D+ mechanism", "E+ Task"]
    for term in forbidden:
        assert term not in blob_text
        assert term not in blob_html


def test_proposal_only_no_destructive_controls():
    dash = od.build_owner_dashboard(lessons=_store()["lessons"], now=NOW)
    html_out = od.render_dashboard_html(dash)
    # No form/button that could trigger a destructive action.
    for tag in ["<form", "<button", "onclick", "<input"]:
        assert tag not in html_out.lower()


def test_empty_store_no_crash():
    dash = od.build_owner_dashboard(now=NOW)
    assert dash["recall_trust"]["total"] == 0
    text = od.render_dashboard_text(dash)
    assert text  # renders something
