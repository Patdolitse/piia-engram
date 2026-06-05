"""Safe Context / Lockdown proposal surface tests."""

from __future__ import annotations

from piia_engram import safe_context


def test_safe_context_redacts_secrets_and_reports_budget_trim():
    payload = {
        "knowledge": [
            {"summary": "api key sk-test_1234567890abcdef1234567890abcdef"},
            {"summary": "x" * 200},
        ],
        "meta": {"project": "demo"},
    }

    out = safe_context.build_safe_context(payload, max_chars=120)

    assert "sk-test_" not in repr(out)
    assert "[REDACTED]" in repr(out)
    assert out["meta"]["safe_context"]["mode"] == "safe"
    assert out["meta"]["safe_context"]["trimmed"] is True


def test_lockdown_context_keeps_counts_not_bodies():
    payload = {
        "identity": {"role": "private role"},
        "recent_activity": {"content": "private recent body"},
        "knowledge": [{"summary": "private lesson body"}],
        "meta": {"context_usage": {"knowledge": {"returned": 1}}},
    }

    out = safe_context.build_safe_context(payload, lockdown=True)

    assert out["identity"] == {}
    assert out["recent_activity"] == {}
    assert out["knowledge"] == []
    assert out["meta"]["safe_context"]["mode"] == "lockdown"
    assert out["meta"]["safe_context"]["knowledge_items_withheld"] == 1
    assert "private lesson body" not in repr(out)
    assert "private recent body" not in repr(out)
