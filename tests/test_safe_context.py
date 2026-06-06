"""Safe Context / Lockdown proposal surface tests."""

from __future__ import annotations

import json

import pytest

from piia_engram import safe_context


def test_safe_context_redacts_secrets_and_reports_budget_trim():
    payload = {
        "knowledge": [
            {"summary": "api key sk-test_1234567890abcdef1234567890abcdef"},
            {"summary": "x" * 200},
        ],
        "meta": {"project": "demo"},
    }

    out = safe_context.build_safe_context(payload, max_chars=260)

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


@pytest.mark.parametrize(
    "secret",
    [
        "sk-test_1234567890abcdef1234567890abcdef",
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "C:\\Users\\owner\\secret-project",
        "owner@example.test",
    ],
)
def test_safe_context_generated_payloads_never_echo_redacted_markers(secret):
    payload = {
        "identity": {"role": f"developer with {secret}"},
        "recent_activity": {"summary": f"recent {secret}"},
        "knowledge": [
            {"summary": f"lesson {idx} {secret}", "detail": "x" * (20 * idx)}
            for idx in range(1, 8)
        ],
        "meta": {"context_usage": {"knowledge": {"returned": 7}}},
    }

    out = safe_context.build_safe_context(payload, max_chars=2000)

    assert secret not in repr(out)
    assert "[REDACTED]" in repr(out)


@pytest.mark.parametrize("max_chars", [240, 360, 700])
def test_safe_context_generated_payloads_respect_reasonable_budget(max_chars):
    payload = {
        "identity": {"role": "developer", "notes": "x" * 500},
        "recent_activity": {"summary": "recent " + "y" * 500},
        "knowledge": [
            {"summary": f"lesson {idx} " + ("z" * 220)}
            for idx in range(8)
        ],
        "meta": {"context_usage": {"knowledge": {"returned": 8}}},
    }

    out = safe_context.build_safe_context(payload, max_chars=max_chars)
    encoded = json.dumps(out, ensure_ascii=False, sort_keys=True)

    assert len(encoded) <= max_chars
    assert out["meta"]["safe_context"]["trimmed"] is True
