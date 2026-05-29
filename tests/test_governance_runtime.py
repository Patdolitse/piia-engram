"""Tests for a0 read-path enforcement (src/piia_engram/governance_runtime.py).

This is the cutover that wires the (already Codex-reviewed) sensitivity
classifier + disclosure gate into the live agent read path, behind the
``ENGRAM_GOVERNANCE`` flag. The security-critical guarantees under test:

* OFF by default — ``governance_enabled()`` is the single guard the MCP tools
  use; when false the tools never call this module (byte-identical reads).
* Enforcement correctness — private/secret items NEVER reach a lower trust
  tier; unlabeled defaults to ``work`` (not public); value-sensitive items
  (benign name, credential/PII value) are gated by content, not just labels.
* Shape preservation — governed output returns the ORIGINAL item objects minus
  the excluded ones; no injected ``sensitivity`` field, no reordering.
* Availability — a corrupt/failed audit ledger must NOT block a correctly
  filtered read.
* Identity — explicit GrantStore binding overrides client-type auto-classify;
  unknown/empty client fails closed; revoked agents get nothing.
"""

from __future__ import annotations

import json

from piia_engram import governance as gov
from piia_engram import governance_runtime as gr
from piia_engram.governance import GovernanceLedger, default_ledger_path
from piia_engram.governance_store import GrantStore


# ── feature flag ──────────────────────────────────────────────────────────


def test_governance_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    assert gr.governance_enabled() is False


def test_governance_flag_truthy_values(monkeypatch):
    for v in ["1", "true", "TRUE", "yes", "On"]:
        monkeypatch.setenv("ENGRAM_GOVERNANCE", v)
        assert gr.governance_enabled() is True
    for v in ["", "0", "false", "no", "off", "maybe"]:
        monkeypatch.setenv("ENGRAM_GOVERNANCE", v)
        assert gr.governance_enabled() is False


def test_current_client_type_reads_env(monkeypatch):
    monkeypatch.delenv("ENGRAM_CLIENT_TYPE", raising=False)
    assert gr.current_client_type() == ""
    monkeypatch.setenv("ENGRAM_CLIENT_TYPE", "  codex  ")
    assert gr.current_client_type() == "codex"


# ── sample knowledge items ─────────────────────────────────────────────────


def _buckets():
    return {
        "lessons": [
            {"id": "L1", "sensitivity": "public", "content": "pub note"},
            {"id": "L3", "sensitivity": "private", "content": "private note"},
            {"id": "L5", "content": "unlabeled note"},  # → defaults to work
        ],
        "decisions": [
            {"id": "D2", "sensitivity": "work", "content": "work choice"},
            {"id": "D4", "sensitivity": "secret", "content": "secret choice"},
        ],
    }


def _ids(items):
    return [i["id"] for i in items]


# ── enforcement correctness (a leak here voids the whole layer) ─────────────


def test_private_self_sees_everything(tmp_path):
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="self"
    )
    assert _ids(out["lessons"]) == ["L1", "L3", "L5"]
    assert _ids(out["decisions"]) == ["D2", "D4"]
    assert receipt["trust_level"] == "private-self"
    assert receipt["excluded_by_sensitivity"] == 0


def test_trusted_local_never_leaks_private_or_secret(tmp_path):
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="claude_code"
    )
    assert _ids(out["lessons"]) == ["L1", "L5"]   # public + unlabeled(=work)
    assert _ids(out["decisions"]) == ["D2"]        # work only
    assert receipt["trust_level"] == "trusted-local"
    assert receipt["excluded_by_sensitivity"] == 2  # L3 private + D4 secret


def test_read_only_external_gets_public_only(tmp_path):
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="some-web-agent"
    )
    assert _ids(out["lessons"]) == ["L1"]
    assert out["decisions"] == []
    assert receipt["trust_level"] == "read-only-external"


def test_unknown_and_empty_client_fail_closed(tmp_path):
    for ct in ["", "totally-unknown-tool"]:
        out, receipt = gr.govern_buckets(
            tmp_path, _buckets(), tool="search_knowledge", client_type=ct
        )
        assert _ids(out["lessons"]) == ["L1"]      # public only
        assert receipt["trust_level"] == "read-only-external"


def test_unlabeled_defaults_to_work_not_public(tmp_path):
    # The unlabeled lesson L5 must NOT reach a public-only agent.
    out, _ = gr.govern_buckets(
        tmp_path, {"lessons": [{"id": "L5", "content": "x"}]},
        tool="search_knowledge", client_type="web",
    )
    assert out["lessons"] == []


def test_value_sensitive_item_gated_by_content_not_label(tmp_path):
    # Benign field name, but the VALUE is a credential -> classified secret by
    # sensitivity.classify_item, so it must be withheld from trusted-local even
    # though it carries no explicit sensitivity label.
    items = {"lessons": [
        {"id": "OK", "content": "just a normal lesson"},
        {"id": "LEAK", "note": "sk-proj-abc123DEFghi456JKLmno789PQRstu0"},
    ]}
    out, _ = gr.govern_buckets(
        tmp_path, items, tool="search_knowledge", client_type="claude_code"
    )
    assert _ids(out["lessons"]) == ["OK"]  # credential-bearing item withheld


# ── shape preservation ──────────────────────────────────────────────────────


def test_returns_original_objects_unmodified(tmp_path):
    buckets = _buckets()
    original_l1 = buckets["lessons"][0]
    out, _ = gr.govern_buckets(
        tmp_path, buckets, tool="search_knowledge", client_type="self"
    )
    # Same object identity (not a copy), and no injected 'sensitivity' field on
    # items that did not already carry one.
    assert out["lessons"][0] is original_l1
    assert "sensitivity" not in out["lessons"][2]  # L5 was unlabeled, stays so


def test_non_list_buckets_passed_through(tmp_path):
    buckets = {"lessons": [{"id": "L1", "sensitivity": "public"}], "meta": {"q": "x"}}
    out, _ = gr.govern_buckets(
        tmp_path, buckets, tool="search_knowledge", client_type="self"
    )
    assert out["meta"] == {"q": "x"}


def test_key_order_preserved(tmp_path):
    out, _ = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="self"
    )
    assert list(out.keys()) == ["lessons", "decisions"]


def test_govern_list_convenience(tmp_path):
    items = [
        {"id": "A", "sensitivity": "public"},
        {"id": "B", "sensitivity": "private"},
    ]
    out, receipt = gr.govern_list(
        tmp_path, items, tool="get_relevant_knowledge", client_type="web"
    )
    assert _ids(out) == ["A"]
    assert receipt["tool"] == "get_relevant_knowledge"


def test_malformed_items_do_not_crash(tmp_path):
    buckets = {"lessons": [{"id": "L1", "sensitivity": "public"}, "not-a-dict", 42, None]}
    out, receipt = gr.govern_buckets(
        tmp_path, buckets, tool="search_knowledge", client_type="self"
    )
    assert _ids(out["lessons"]) == ["L1"]          # the one valid dict survives
    assert receipt["excluded_malformed"] == 3


# ── identity: grants & revocation ───────────────────────────────────────────


def test_explicit_grant_overrides_client_type(tmp_path):
    # An unknown client would normally be read-only-external, but an explicit
    # grant for that agent_id lifts it to private-self.
    GrantStore(tmp_path).set_grant("agent-007", "private-self")
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge",
        agent_id="agent-007", client_type="unknown-tool",
    )
    assert _ids(out["lessons"]) == ["L1", "L3", "L5"]
    assert receipt["trust_level"] == "private-self"


def test_revoked_agent_gets_nothing(tmp_path):
    GrantStore(tmp_path).set_grant("agent-x", "private-self")
    GrantStore(tmp_path).revoke("agent-x")
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", agent_id="agent-x"
    )
    assert out["lessons"] == [] and out["decisions"] == []
    assert receipt["revoked"] is True


# ── audit ledger ────────────────────────────────────────────────────────────


def test_disclosure_is_logged_and_chain_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="claude_code"
    )
    assert receipt["audit_logged"] is True
    ledger = GovernanceLedger(default_ledger_path(tmp_path))
    ok, _msg = ledger.verify()
    assert ok
    recs = ledger.records()
    assert recs and recs[-1]["event"]["kind"] == "disclosure"
    assert recs[-1]["event"]["tool"] == "search_knowledge"
    assert recs[-1]["event"]["returned_by_type"] == {"lessons": 2, "decisions": 1}


def test_corrupt_ledger_does_not_block_read(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    led = default_ledger_path(tmp_path)
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text("{ this is not valid json\n", encoding="utf-8")
    # The read must still return correctly-filtered items; only the audit fails.
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="claude_code"
    )
    assert _ids(out["lessons"]) == ["L1", "L5"]   # filtering still correct
    assert receipt["audit_logged"] is False
    assert "audit_error" in receipt


def test_receipt_is_json_serializable(tmp_path):
    _out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="claude_code"
    )
    json.dumps(receipt)  # must not raise
