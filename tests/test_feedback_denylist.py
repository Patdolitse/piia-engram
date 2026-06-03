"""L1: send_feedback content-denylist hardening (privacy at the send boundary).

These prove the feedback-report allowlist + content guard rejects content-like
fields *before* network serialization, while a counts/distribution-only report
is accepted up to the (mocked) network boundary. The guard is independent of how
the report was built — the remote worker persists the raw payload, so the client
allowlist is the privacy gate.
"""

from __future__ import annotations

import pytest

from piia_engram import telemetry
from piia_engram import telemetry_validation as tv
from piia_engram import setup_wizard


# --- the validator: shape-level allowlist + content checks -------------------

def _valid_report() -> dict:
    """A realistic counts/distribution-only report (mirrors _build_feedback_report)."""
    return {
        "report_type": "engram_beta_feedback",
        "report_version": 1,
        "generated_at": "2026-06-03T08:00:00+00:00",
        "engram_version": "3.45.3",
        "os": "Windows",
        "python": "3.12.0",
        "knowledge": {
            "total": 42, "staging": 12, "verified": 30, "promotion_rate": 0.71,
            "lessons": {"staging": 6, "verified": 18},
            "decisions": {"staging": 4, "verified": 9},
            "playbooks": {"staging": 2, "verified": 3},
        },
        "top_domains": {"ai": 10, "engram": 7, "machine learning": 3},
        "source_tools": {"claude_code": 20, "codex": 12, "unknown": 1},
        "first_knowledge_date": "2026-01-02",
        "days_with_knowledge": 152,
        "avg_staging_age_days": 4.2,
        "session_count": 88,
        "top_mcp_tools": {"add_lesson": 40, "search_knowledge": 31},
        "configured_tools": ["claude_code", "codex"],
        "beta_events": {"events": {"knowledge_created": 30, "knowledge_promoted": 12}},
    }


def test_valid_counts_report_accepted():
    ok, problems = tv.validate_feedback_report(_valid_report())
    assert ok, problems


def test_real_builder_output_passes_allowlist(tmp_path, monkeypatch):
    """The actual _build_feedback_report output must satisfy the guard so valid
    feedback behavior is preserved."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    # Seed a small store so the report has real distributions, not just empties.
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir(parents=True)
    import json
    (knowledge / "lessons.json").write_text(json.dumps([
        {"id": "l1", "summary": "x", "domain": "ai", "tier": "verified",
         "source_tool": "claude_code", "created_at": "2026-05-01T00:00:00+00:00"},
        {"id": "l2", "summary": "y", "domain": "engram,ai", "tier": "staging",
         "source_tool": "codex", "created_at": "2026-05-20T00:00:00+00:00"},
    ], ensure_ascii=False), encoding="utf-8")
    (knowledge / "decisions.json").write_text(json.dumps([
        {"id": "d1", "question": "q", "choice": "c", "domain": "ai",
         "tier": "verified", "source_tool": "claude_code",
         "created_at": "2026-04-01T00:00:00+00:00"},
    ], ensure_ascii=False), encoding="utf-8")
    # Seed beta events INCLUDING a blank source_tool so beta_events carries an
    # empty-string ("unidentified source") tag — the realistic shape that must
    # NOT cause the whole report to be rejected (regression guard).
    (tmp_path / "beta_events.jsonl").write_text("\n".join(
        json.dumps(e, ensure_ascii=False) for e in [
            {"event": "knowledge_created", "ts": "2026-05-01T00:00:00+00:00",
             "d": {"source_tool": "", "domain": "ai", "tier": "staging"}},
            {"event": "knowledge_created", "ts": "2026-05-02T00:00:00+00:00",
             "d": {"source_tool": "claude_code", "domain": "engram", "tier": "verified"}},
        ]) + "\n", encoding="utf-8")

    report = setup_wizard._build_feedback_report(str(tmp_path))
    ok, problems = tv.validate_feedback_report(report)
    assert ok, problems
    # Sanity: the report carries the count surfaces it should, including the
    # populated beta_events with a blank-source-tool tag.
    assert report["knowledge"]["total"] == 3
    assert "beta_events" in report
    assert "" in report["beta_events"].get("created_by_tool", {})


@pytest.mark.parametrize("mutate, why", [
    (lambda r: r.update({"summary": "a long lesson body that should never ship"}), "disallowed_key"),
    (lambda r: r.update({"raw_notes": "secret"}), "disallowed_key"),
    (lambda r: r.update({"os": "C:\\Users\\alice\\secret\\notes.txt"}), "path_value"),
    (lambda r: r.update({"engram_version": "see https://evil.example.com/leak"}), "url_value"),
    (lambda r: r.update({"python": "contact me at alice@example.com"}), "email_value"),
    (lambda r: r.update({"first_knowledge_date": "this is a long natural language sentence leaking content"}), "free_text"),
    (lambda r: r.update({"engram_version": "x" * 200}), "too_long"),
    (lambda r: r["top_domains"].update({"/etc/passwd": 1}), "nested_path_key"),
    (lambda r: r["beta_events"].update({"leak": {"note": "a full sentence of free text leaking user content here"}}), "nested_free_text"),
    (lambda r: r["source_tools"].update({"tool": "alice@example.com"}), "nested_email_value"),
])
def test_content_like_fields_rejected(mutate, why):
    report = _valid_report()
    mutate(report)
    ok, problems = tv.validate_feedback_report(report)
    assert ok is False, f"{why}: expected rejection, got accept"
    assert problems


def test_non_dict_report_rejected():
    ok, problems = tv.validate_feedback_report(["not", "a", "dict"])
    assert ok is False
    assert problems


def test_empty_string_tag_is_accepted():
    # The "unidentified source" bucket: builders emit "" keys; that is not
    # content and must not sink an otherwise-valid report.
    report = _valid_report()
    report["source_tools"][""] = 5
    report["beta_events"]["created_by_tool"] = {"": 3, "claude_code": 9}
    ok, problems = tv.validate_feedback_report(report)
    assert ok, problems


@pytest.mark.parametrize("tag, why", [
    ("客户张伟的并购计划2026年Q3全部完成交割", "cjk_sentence_tag"),
    ("the secret merger deal closes next quarter soon", "english_sentence_tag"),
    ("alice﹫example.com", "homoglyph_email_tag"),
    ("x" * 60, "overlong_tag"),
])
def test_content_bearing_nested_tag_keys_rejected(tag, why):
    report = _valid_report()
    report["top_domains"] = {tag: 3}
    ok, problems = tv.validate_feedback_report(report)
    assert ok is False, why
    assert problems


@pytest.mark.parametrize("value, why", [
    ("客户并购计划2026年第三季度完成交割并支付全部对价", "cjk_value"),
    ("alice﹫example.com", "homoglyph_email_value"),
    ("x" * 70, "value_over_64"),
    ("the secret merger deal closes next quarter", "six_word_value"),
])
def test_content_bearing_values_rejected(value, why):
    report = _valid_report()
    report["os"] = value
    ok, problems = tv.validate_feedback_report(report)
    assert ok is False, why
    assert problems


def test_short_legitimate_tags_still_accepted():
    # Real coarse tags — English multiword, short CJK, versions — must pass.
    report = _valid_report()
    report["top_domains"] = {"machine learning": 4, "前端开发": 7, "infra": 2}
    report["source_tools"] = {"claude_code": 10, "codex": 3}
    ok, problems = tv.validate_feedback_report(report)
    assert ok, problems


# --- the send boundary: reject before any network serialization --------------

class _SpyURLOpen:
    """Stand-in for urllib's urlopen that records whether it was called."""

    def __init__(self, status=200):
        self.calls = 0
        self.status = status

    def __call__(self, req, timeout=None):
        self.calls += 1
        self._last = req
        return self

    # context-manager protocol used by send_feedback's `with urlopen(...) as resp`
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def feedback_send_env(monkeypatch):
    """Enable feedback + stub config persistence, so only the guard / network
    decide the outcome."""
    monkeypatch.setattr(telemetry, "is_feedback_enabled", lambda: True)
    monkeypatch.setattr(telemetry, "_load_config", lambda: {"local_uuid": "uuid-abc"})
    monkeypatch.setattr(telemetry, "_save_config", lambda cfg: None)


def test_bad_report_rejected_before_network(feedback_send_env, monkeypatch):
    spy = _SpyURLOpen()
    monkeypatch.setattr(telemetry, "urlopen", spy)

    bad = _valid_report()
    bad["leaked_body"] = "a long natural-language lesson body that must never be sent"

    assert telemetry.send_feedback(bad) is False
    assert spy.calls == 0, "network must not be touched for a rejected report"


def test_valid_report_reaches_network_boundary(feedback_send_env, monkeypatch):
    spy = _SpyURLOpen(status=200)
    monkeypatch.setattr(telemetry, "urlopen", spy)

    assert telemetry.send_feedback(_valid_report()) is True
    assert spy.calls == 1, "a valid counts-only report must reach the send boundary"


def test_injected_daily_id_does_not_break_validation(feedback_send_env, monkeypatch):
    # daily_id is injected by send_feedback and is itself allowlisted; a valid
    # report still passes after the (controlled) hash is added.
    report = _valid_report()
    report["daily_id"] = "deadbeefcafe"
    ok, problems = tv.validate_feedback_report(report)
    assert ok, problems
