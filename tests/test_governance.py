"""Tests for a0 governance scaffold (src/piia_engram/governance.py).

Covers trust classification, the disclosure gate (the enforcement-correctness
cases — a leak here voids the whole 'neutral custodian' value), the
disclosure receipt, and the append-only hash-chained ledger incl. tamper
detection.
"""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram import governance as gov


# ── trust classification ────────────────────────────────────────────────


def test_known_local_clients_map_to_trusted_local():
    for c in ["claude_code", "claude-code", "codex", "cursor", "windsurf"]:
        assert gov.classify_agent(c) == "trusted-local"


def test_self_clients_map_to_private_self():
    for c in ["self", "cli", "doctor"]:
        assert gov.classify_agent(c) == "private-self"


def test_unknown_and_empty_fail_closed_to_read_only_external():
    for c in ["", None, "some-random-tool", "evil-agent"]:
        assert gov.classify_agent(c) == "read-only-external"


# ── disclosure gate (enforcement correctness) ────────────────────────────


def _items():
    return [
        {"id": "1", "sensitivity": "public", "type": "lesson"},
        {"id": "2", "sensitivity": "work", "type": "decision"},
        {"id": "3", "sensitivity": "private", "type": "lesson"},
        {"id": "4", "sensitivity": "secret", "type": "decision"},
        {"id": "5", "type": "lesson"},  # unlabeled → treated as "work"
    ]


def test_private_self_sees_everything():
    allowed, receipt = gov.gate(_items(), "private-self")
    assert {i["id"] for i in allowed} == {"1", "2", "3", "4", "5"}
    assert receipt["excluded_by_sensitivity"] == 0


def test_trusted_local_never_leaks_private_or_secret():
    allowed, receipt = gov.gate(_items(), "trusted-local")
    ids = {i["id"] for i in allowed}
    assert ids == {"1", "2", "5"}            # public + work + unlabeled(=work)
    assert "3" not in ids and "4" not in ids  # private/secret NEVER leak
    assert receipt["excluded_by_sensitivity"] == 2


def test_read_only_external_gets_public_only():
    allowed, _ = gov.gate(_items(), "read-only-external")
    assert {i["id"] for i in allowed} == {"1"}


def test_unlabeled_item_defaults_to_work_not_public():
    # item 5 (unlabeled) must NOT reach a public-only agent
    allowed, _ = gov.gate([{"id": "5", "type": "lesson"}], "read-only-external")
    assert allowed == []


def test_unknown_sensitivity_label_fails_closed():
    weird = [{"id": "x", "sensitivity": "ultra-mega-secret", "type": "lesson"}]
    allowed, _ = gov.gate(weird, "trusted-local")
    assert allowed == []  # unknown label treated as most sensitive


def test_unknown_trust_level_falls_back_to_most_restrictive():
    allowed, receipt = gov.gate(_items(), "bogus-level")
    assert {i["id"] for i in allowed} == {"1"}  # behaves as read-only-external
    assert receipt["trust_level"] == "read-only-external"


def test_gate_handles_non_dict_items_without_crashing():
    # fail-safe: a None / str mixed into items must not crash the gate
    allowed, receipt = gov.gate(
        [None, "junk", {"id": "pub", "sensitivity": "public", "type": "lesson"}],
        "read-only-external",
    )
    assert {i["id"] for i in allowed} == {"pub"}
    assert receipt["excluded_malformed"] == 2


def test_revoked_agent_gets_nothing():
    allowed, receipt = gov.gate(_items(), "trusted-local", agent_id="codex", revoked=True)
    assert allowed == []
    assert receipt["revoked"] is True
    assert receipt["returned_count"] == 0


def test_receipt_shape_and_counts():
    allowed, receipt = gov.gate(_items(), "trusted-local",
                                agent_id="codex-cli", client_type="codex",
                                declared_task="fix the search bug")
    assert receipt["returned_count"] == len(allowed) == 3
    assert receipt["returned_by_type"] == {"lesson": 2, "decision": 1}
    assert receipt["agent_id"] == "codex-cli"
    assert receipt["declared_task"] == "fix the search bug"
    assert receipt["receipt_id"].startswith("ctx_")


# ── append-only hash-chained ledger ──────────────────────────────────────


def test_ledger_append_and_verify_ok(tmp_path):
    led = gov.GovernanceLedger(tmp_path / "gl.jsonl")
    led.append({"receipt_id": "a", "agent": "codex"})
    led.append({"receipt_id": "b", "agent": "claude_code"})
    ok, msg = led.verify()
    assert ok, msg
    assert "2 events" in msg


def test_ledger_seq_and_chain_links(tmp_path):
    led = gov.GovernanceLedger(tmp_path / "gl.jsonl")
    r0 = led.append({"x": 1})
    r1 = led.append({"x": 2})
    assert r0["seq"] == 0 and r1["seq"] == 1
    assert r0["prev_hash"] == gov.GovernanceLedger.GENESIS
    assert r1["prev_hash"] == r0["hash"]  # chain links


def test_ledger_detects_tampering(tmp_path):
    p = tmp_path / "gl.jsonl"
    led = gov.GovernanceLedger(p)
    led.append({"receipt_id": "a", "secret_given": False})
    led.append({"receipt_id": "b"})
    # tamper: flip a field in the first record's event, keep its old hash
    lines = p.read_text(encoding="utf-8").splitlines()
    rec0 = json.loads(lines[0])
    rec0["event"]["secret_given"] = True   # rewrite history
    lines[0] = json.dumps(rec0, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, msg = led.verify()
    assert ok is False
    assert "hash mismatch" in msg or "tampered" in msg


def test_ledger_detects_timestamp_tampering(tmp_path):
    # P1a fix: the hash must cover ts, so back-dating a record is detected.
    p = tmp_path / "gl.jsonl"
    led = gov.GovernanceLedger(p)
    led.append({"receipt_id": "a"})
    led.append({"receipt_id": "b"})
    lines = p.read_text(encoding="utf-8").splitlines()
    rec0 = json.loads(lines[0])
    rec0["ts"] = "1999-01-01T00:00:00"      # back-date, keep old hash
    lines[0] = json.dumps(rec0, ensure_ascii=False)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, msg = led.verify()
    assert ok is False and "tampered" in msg


def test_ledger_corrupt_tail_fails_closed(tmp_path):
    # P2a fix: a broken tail line must raise LedgerCorruptionError on append,
    # not a bare JSONDecodeError, and not silently extend a broken chain.
    p = tmp_path / "gl.jsonl"
    led = gov.GovernanceLedger(p)
    led.append({"receipt_id": "a"})
    with open(p, "a", encoding="utf-8") as f:
        f.write("{ this is a half-written corrupt line\n")
    import pytest
    with pytest.raises(gov.LedgerCorruptionError):
        led.append({"receipt_id": "b"})


def test_ledger_concurrent_instances_append_consistently(tmp_path):
    # the lock keeps read-last+write atomic; sequential appends from two
    # instances on the same file stay a valid chain.
    p = tmp_path / "gl.jsonl"
    a = gov.GovernanceLedger(p)
    b = gov.GovernanceLedger(p)
    a.append({"i": 1})
    b.append({"i": 2})
    a.append({"i": 3})
    ok, msg = b.verify()
    assert ok, msg
    assert [r["seq"] for r in b.records()] == [0, 1, 2]


def test_ledger_detects_reorder_or_seq_gap(tmp_path):
    p = tmp_path / "gl.jsonl"
    led = gov.GovernanceLedger(p)
    led.append({"x": 1})
    led.append({"x": 2})
    led.append({"x": 3})
    lines = p.read_text(encoding="utf-8").splitlines()
    # drop the middle line → seq gap + broken chain
    p.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
    ok, _ = led.verify()
    assert ok is False


def test_default_ledger_path_honors_root(tmp_path, monkeypatch):
    monkeypatch.delenv("ENGRAM_DIR", raising=False)
    assert gov.default_ledger_path(tmp_path) == tmp_path / "governance_ledger.jsonl"
