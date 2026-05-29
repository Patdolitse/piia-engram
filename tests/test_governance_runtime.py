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


# ── R15 P2: identity resolution fails closed ─────────────────────────────────


def test_corrupt_grants_file_fails_closed_to_read_only_external(tmp_path):
    # A damaged grants.json must NOT raise (that would DoS every governed read);
    # it must drop to the most restrictive tier and surface the failure.
    store_path = GrantStore(tmp_path).path
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{ not valid json", encoding="utf-8")

    # Even a client that WOULD be trusted (self) falls back to public-only.
    out, receipt = gr.govern_buckets(
        tmp_path, _buckets(), tool="search_knowledge", client_type="self"
    )
    assert _ids(out["lessons"]) == ["L1"]      # public only
    assert out["decisions"] == []
    assert receipt["trust_level"] == "read-only-external"
    assert receipt["grant_error"]               # failure surfaced, not swallowed


def test_corrupt_grants_file_does_not_raise_for_any_identity(tmp_path):
    store_path = GrantStore(tmp_path).path
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("}{ corrupt", encoding="utf-8")
    for kw in ({"client_type": "self"}, {"agent_id": "agent-007"}, {"client_type": ""}):
        out, receipt = gr.govern_buckets(
            tmp_path, _buckets(), tool="search_knowledge", **kw
        )
        assert _ids(out["lessons"]) == ["L1"]   # public only, never throws
        assert receipt["trust_level"] == "read-only-external"


# ── R15 P3: invalid sensitivity label fails closed (not silently → work) ─────


def test_invalid_sensitivity_label_withheld_from_trusted_local(tmp_path):
    # A present-but-bogus label must NOT be normalized to the 'work' default
    # (which trusted-local can read). It is treated as the most sensitive tier.
    items = {"lessons": [
        {"id": "OK", "sensitivity": "work", "content": "normal"},
        {"id": "BOGUS", "sensitivity": "definitely-not-a-level", "content": "x"},
    ]}
    out, _ = gr.govern_buckets(
        tmp_path, items, tool="search_knowledge", client_type="claude_code"
    )
    assert _ids(out["lessons"]) == ["OK"]       # bogus-label item withheld


def test_invalid_label_still_visible_to_private_self(tmp_path):
    items = {"lessons": [{"id": "BOGUS", "sensitivity": "weird", "content": "x"}]}
    out, _ = gr.govern_buckets(
        tmp_path, items, tool="search_knowledge", client_type="self"
    )
    assert _ids(out["lessons"]) == ["BOGUS"]    # owner still sees it


def test_unlabeled_still_treated_as_work_not_secret(tmp_path):
    # The fail-close applies only to PRESENT-but-invalid labels; a truly
    # unlabeled item keeps the 'work' default and reaches trusted-local.
    out, _ = gr.govern_buckets(
        tmp_path, {"lessons": [{"id": "U", "content": "x"}]},
        tool="search_knowledge", client_type="claude_code",
    )
    assert _ids(out["lessons"]) == ["U"]


# ── owner-only gate (aggregate/dump views) ───────────────────────────────────


def test_owner_only_returns_string_dump_to_private_self(tmp_path):
    out, receipt = gr.govern_owner_only(
        tmp_path, "FULL REPORT BODY", tool="export_knowledge_report", client_type="self"
    )
    assert out == "FULL REPORT BODY"
    assert receipt["returned_by_type"] == {"_owner_only": 1}


def test_owner_only_refuses_string_dump_for_external(tmp_path):
    out, receipt = gr.govern_owner_only(
        tmp_path, "FULL REPORT BODY", tool="export_knowledge_report", client_type="web"
    )
    assert "FULL REPORT BODY" not in out
    assert isinstance(out, str)                 # refusal string, not the body
    assert receipt["returned_by_type"] == {"_owner_only": 0}


def test_owner_only_refuses_dict_with_withheld_stub_for_external(tmp_path):
    out, _ = gr.govern_owner_only(
        tmp_path, {"digest": {"secret": "s"}}, tool="get_knowledge_overview",
        client_type="web",
    )
    assert out["governance_withheld"] is True
    assert "secret" not in json.dumps(out)


def test_owner_only_withholds_from_trusted_local_too(tmp_path):
    # Aggregate views are private-self ONLY — even trusted-local is refused,
    # because the granular per-item tools remain available and filtered.
    out, _ = gr.govern_owner_only(
        tmp_path, "BODY", tool="get_resume_brief", client_type="claude_code"
    )
    assert out != "BODY"


# ── govern_result: mixed dicts (named list + item fields) ────────────────────


def test_govern_result_filters_list_field_passes_scalars(tmp_path):
    payload = {
        "description": "d", "total": 2, "recommended_domains": ["python"],
        "items": [
            {"id": "A", "sensitivity": "public"},
            {"id": "B", "sensitivity": "secret"},
        ],
    }
    out, _ = gr.govern_result(
        tmp_path, payload, tool="get_knowledge_inheritance",
        list_fields=("items",), client_type="web",
    )
    assert _ids(out["items"]) == ["A"]          # secret filtered
    assert out["recommended_domains"] == ["python"]  # scalar list untouched
    assert out["description"] == "d"


def test_govern_result_item_field_replaced_by_stub_when_over_ceiling(tmp_path):
    payload = {"source": {"id": "S", "sensitivity": "secret"}, "related": []}
    out, _ = gr.govern_result(
        tmp_path, payload, tool="get_related_knowledge",
        list_fields=("related",), item_fields=("source",), client_type="web",
    )
    assert out["source"]["governance_withheld"] is True


def test_govern_result_item_field_preserved_for_owner(tmp_path):
    src = {"id": "S", "sensitivity": "secret"}
    payload = {"source": src, "related": []}
    out, _ = gr.govern_result(
        tmp_path, payload, tool="get_related_knowledge",
        item_fields=("source",), client_type="self",
    )
    assert out["source"] is src                 # original object, not a stub


def test_maybe_helpers_are_noop_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    items = [{"id": "B", "sensitivity": "secret"}]
    assert gr.maybe_govern_list(tmp_path, items, tool="t") is items
    buckets = {"lessons": items}
    assert gr.maybe_govern_buckets(tmp_path, buckets, tool="t") is buckets
    payload = {"items": items}
    assert gr.maybe_govern_result(tmp_path, payload, tool="t", list_fields=("items",)) is payload
    assert gr.maybe_govern_owner_only(tmp_path, "BODY", tool="t") == "BODY"
    one = {"id": "B", "sensitivity": "secret"}
    assert gr.maybe_govern_one(tmp_path, one, tool="t") is one
    ack = {"success": True, "primary_title": "SECRET TITLE"}
    assert gr.maybe_govern_write_ack(tmp_path, ack, tool="t") is ack
    assert gr.maybe_govern_write_ack(tmp_path, "Linked: SECRET", tool="t") == "Linked: SECRET"
    assert gr.maybe_refuse_export(tmp_path, tool="export_engram") is None
    assert gr.caller_is_owner(tmp_path) is True   # no restriction when OFF


# ── R16 P1: write-echo acks must not disclose stored title/body ──────────────


def test_write_ack_dict_owner_gets_full_payload(tmp_path):
    payload = {"success": True, "primary_title": "SECRET TITLE", "secondary_title": "other"}
    out, receipt = gr.govern_write_ack(
        tmp_path, payload, tool="merge_knowledge", client_type="self"
    )
    assert out is payload                          # owner sees the stored titles
    assert receipt["returned_by_type"] == {"_write_ack": 1}


def test_write_ack_dict_withholds_stored_title_from_external(tmp_path):
    payload = {"success": True, "primary_title": "SECRET TITLE", "secondary_title": "other"}
    out, receipt = gr.govern_write_ack(
        tmp_path, payload, tool="merge_knowledge", client_type="web"
    )
    blob = json.dumps(out)
    assert "SECRET TITLE" not in blob and "other" not in blob
    assert out["governance_withheld"] is True
    assert out["success"] is True                  # caller still learns it worked
    assert receipt["returned_by_type"] == {"_write_ack": 0}


def test_write_ack_dict_withholds_from_trusted_local_too(tmp_path):
    # Title is stored content the caller never supplied — only the owner sees it.
    payload = {"success": True, "primary_title": "SECRET TITLE"}
    out, _ = gr.govern_write_ack(
        tmp_path, payload, tool="merge_knowledge", client_type="claude_code"
    )
    assert "SECRET TITLE" not in json.dumps(out)
    assert out["governance_withheld"] is True


def test_write_ack_string_withheld_from_external(tmp_path):
    out, _ = gr.govern_write_ack(
        tmp_path, "Playbook 已更新: SECRET TITLE", tool="update_playbook", client_type="web"
    )
    assert "SECRET TITLE" not in out
    assert isinstance(out, str)


def test_write_ack_error_payload_passes_through(tmp_path):
    # Error dicts echo only the caller's own IDs/shape, so they pass unchanged
    # even for external callers — the caller must learn the op failed.
    err = {"error": "Playbook not found: pb-123"}
    out, _ = gr.govern_write_ack(
        tmp_path, err, tool="update_playbook", client_type="web"
    )
    assert out is err


# ── R16: pre-write export owner gate (refuse before writing the file) ────────


def test_refuse_export_returns_refusal_for_external(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    refusal = gr.maybe_refuse_export(
        tmp_path, tool="export_engram", client_type="web"
    )
    assert isinstance(refusal, str) and refusal       # caller returns this, skips write
    # Bilingual governance refusal, no store content.
    assert "治理" in refusal or "Governance" in refusal


def test_refuse_export_returns_none_for_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    assert gr.maybe_refuse_export(
        tmp_path, tool="export_engram", client_type="self"
    ) is None


def test_refuse_export_noop_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_GOVERNANCE", raising=False)
    assert gr.maybe_refuse_export(
        tmp_path, tool="export_engram", client_type="web"
    ) is None                                         # OFF → never refuses


def test_refuse_export_withholds_from_trusted_local(tmp_path, monkeypatch):
    # Full-store export is private-self only; even trusted-local is refused.
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    refusal = gr.maybe_refuse_export(
        tmp_path, tool="export_engram", client_type="claude_code"
    )
    assert isinstance(refusal, str) and refusal


def test_refuse_export_logs_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    gr.maybe_refuse_export(tmp_path, tool="export_engram", client_type="web")
    ledger = GovernanceLedger(default_ledger_path(tmp_path))
    recs = ledger.records()
    assert recs and recs[-1]["event"]["tool"] == "export_engram"


# ── caller_is_owner ──────────────────────────────────────────────────────────


def test_caller_is_owner_true_only_for_private_self(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    assert gr.caller_is_owner(tmp_path, client_type="self") is True
    assert gr.caller_is_owner(tmp_path, client_type="claude_code") is False
    assert gr.caller_is_owner(tmp_path, client_type="web") is False
    assert gr.caller_is_owner(tmp_path, client_type="") is False


def test_caller_is_owner_false_when_revoked(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_GOVERNANCE", "1")
    GrantStore(tmp_path).set_grant("agent-x", "private-self")
    GrantStore(tmp_path).revoke("agent-x")
    assert gr.caller_is_owner(tmp_path, agent_id="agent-x") is False


# ── R16 P3: empty / whitespace / non-string sensitivity → secret (fail-closed) ─


def test_empty_string_sensitivity_treated_as_secret(tmp_path):
    # A present-but-empty label is NOT a true "unlabeled" item; it must not be
    # normalized to the readable 'work' default.
    items = {"lessons": [
        {"id": "OK", "content": "x"},                       # truly unlabeled → work
        {"id": "EMPTY", "sensitivity": "", "content": "x"},
        {"id": "BLANK", "sensitivity": "   ", "content": "x"},
    ]}
    out, _ = gr.govern_buckets(
        tmp_path, items, tool="search_knowledge", client_type="claude_code"
    )
    assert _ids(out["lessons"]) == ["OK"]   # empty/blank labels withheld


def test_non_string_sensitivity_treated_as_secret(tmp_path):
    items = {"lessons": [
        {"id": "NUM", "sensitivity": 123, "content": "x"},
        {"id": "LIST", "sensitivity": ["work"], "content": "x"},
    ]}
    out, _ = gr.govern_buckets(
        tmp_path, items, tool="search_knowledge", client_type="claude_code"
    )
    assert out["lessons"] == []   # both withheld as secret


def test_valid_label_case_insensitive_still_readable(tmp_path):
    # Whitespace/case-variant of a VALID level normalizes and stays readable.
    items = {"lessons": [
        {"id": "PAD", "sensitivity": "  work  ", "content": "x"},
        {"id": "CAPS", "sensitivity": "WORK", "content": "x"},
    ]}
    out, _ = gr.govern_buckets(
        tmp_path, items, tool="search_knowledge", client_type="claude_code"
    )
    assert _ids(out["lessons"]) == ["PAD", "CAPS"]
