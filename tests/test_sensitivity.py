"""Tests for sensitivity classification (src/piia_engram/sensitivity.py).

Emphasis: SAFE BY DEFAULT WITH ZERO CONFIG — PII + credentials protected
even when the user has set no restricted_fields.
"""

from __future__ import annotations

from piia_engram import sensitivity as sv


# ── zero-config safety (the product-owner requirement) ───────────────────


def test_pii_is_private_with_zero_config():
    for f in ["email", "phone", "address", "location", "real_name", "id_number"]:
        assert sv.classify_field(f) == "private", f

def test_credential_shaped_names_are_secret_with_zero_config():
    for f in ["password", "api_key", "apiKey", "access_token", "ENGRAM_SECRET",
              "db_credential", "private_key"]:
        assert sv.classify_field(f) == "secret", f

def test_ordinary_fields_default_to_work_not_public():
    for f in ["role", "language", "technical_level", "description"]:
        assert sv.classify_field(f) == "work", f


# ── restricted_fields is additive (power users) ──────────────────────────


def test_restricted_fields_raise_to_private():
    # a field that would otherwise be "work" becomes private when restricted
    assert sv.classify_field("description") == "work"
    assert sv.classify_field("description", restricted_fields=["description"]) == "private"

def test_restricted_fields_cannot_lower_the_builtin_floor():
    # even if a credential-shaped field is NOT in restricted_fields, it stays secret
    assert sv.classify_field("api_key", restricted_fields=[]) == "secret"


# ── knowledge items ──────────────────────────────────────────────────────

def test_secret_field_cannot_be_marked_public_LEAK_REGRESSION():
    # Codex round-3 P1: an item with a credential field + explicit public
    # must NOT stay public (it would leak to read-only-external).
    item = {"id": "3", "sensitivity": "public", "api_key": "sk-abc"}
    assert sv.classify_item(item) == "secret"

def test_pii_field_floors_item_to_private():
    item = {"id": "x", "sensitivity": "public", "email": "a@b.c"}
    assert sv.classify_item(item) == "private"

def test_explicit_public_honored_when_no_sensitive_fields():
    assert sv.classify_item({"id": "1", "summary": "x", "sensitivity": "public"}) == "public"

def test_field_name_separator_normalization():
    # Codex round-3 P2: api-key / private-key / api.key must be detected
    for f in ["api-key", "api.key", "API Key", "private-key", "private.key"]:
        assert sv.classify_field(f) == "secret", f


def test_annotate_then_gate_blocks_secret_field_item():
    # end-to-end leak regression through annotate -> gate
    from piia_engram import governance as gov
    items = [{"id": "ok", "summary": "fine", "sensitivity": "public"},
             {"id": "leak", "sensitivity": "public", "access_token": "t"}]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"ok"}  # the token item must be blocked


def test_item_defaults_to_work():
    assert sv.classify_item({"id": "1", "summary": "x"}) == "work"

def test_item_honors_explicit_valid_level():
    assert sv.classify_item({"sensitivity": "public"}) == "public"
    assert sv.classify_item({"sensitivity": "secret"}) == "secret"

def test_item_invalid_level_falls_back_to_work():
    assert sv.classify_item({"sensitivity": "ultra"}) == "work"

def test_non_dict_item_is_work():
    assert sv.classify_item(None) == "work"


# ── annotate_items feeds the gate ────────────────────────────────────────

def test_annotate_sets_sensitivity_and_preserves_nondict():
    items = [{"id": "1", "summary": "a"}, None, {"id": "2", "sensitivity": "public"}]
    out = sv.annotate_items(items)
    assert out[0]["sensitivity"] == "work"
    assert out[1] is None
    assert out[2]["sensitivity"] == "public"
    # originals not mutated
    assert "sensitivity" not in items[0]


def test_annotate_then_gate_blocks_pii_for_external(monkeypatch):
    # end-to-end: an item marked private (e.g. derived from a PII field) must
    # not reach a read-only-external agent after annotate→gate.
    from piia_engram import governance as gov
    items = [{"id": "pub", "sensitivity": "public"}, {"id": "sec", "sensitivity": "private"}]
    allowed, _ = gov.gate(sv.annotate_items(items), "read-only-external")
    assert {i["id"] for i in allowed} == {"pub"}
