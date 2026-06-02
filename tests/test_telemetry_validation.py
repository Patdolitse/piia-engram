"""Tests for telemetry contract validation + opt-in/no-content guards (Phase 13)."""

from __future__ import annotations

from pathlib import Path

import pytest

from piia_engram import telemetry, telemetry_validation as tv

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "worker"


# --- static contract consistency against the real worker ---------------------

def test_real_contract_is_consistent():
    report = tv.validate_telemetry_contract(WORKER)
    assert report["ok"], report["problems"]


def test_schema_has_v1_1_columns():
    cols = tv.parse_schema_columns((WORKER / "schema.sql").read_text(encoding="utf-8"))
    assert tv.V1_1_DERIVED_COLUMNS.issubset(cols)


def test_v1_1_migration_adds_exactly_the_derived_buckets():
    mig = (WORKER / "migrations" / "20260603_telemetry_contract_v1_1.sql").read_text(encoding="utf-8")
    added = tv.parse_added_columns(mig)
    assert tv.V1_1_DERIVED_COLUMNS.issubset(added)


def test_v1_1_migration_is_additive():
    mig = (WORKER / "migrations" / "20260603_telemetry_contract_v1_1.sql").read_text(encoding="utf-8")
    ok, problems = tv.validate_migration_additive(mig)
    assert ok, problems


def test_no_content_columns_in_real_schema():
    cols = tv.parse_schema_columns((WORKER / "schema.sql").read_text(encoding="utf-8"))
    assert tv._content_markers_in(cols) == []


def test_no_content_fields_in_payload_contract():
    assert tv._content_markers_in(set(tv.PAYLOAD_TO_COLUMN)) == []


# --- the validator catches drift ---------------------------------------------

def test_validator_flags_destructive_migration():
    ok, problems = tv.validate_migration_additive("DROP COLUMN version_adoption;")
    assert ok is False
    assert any("DROP COLUMN" in p for p in problems)


def test_validator_allows_upsert_and_comments():
    # An additive ON CONFLICT DO UPDATE upsert and a comment mentioning 'update'
    # must NOT be flagged as destructive.
    additive = (
        "-- update the rollup index after insert\n"
        "INSERT INTO events (daily_id) VALUES ('x')\n"
        "  ON CONFLICT(daily_id) DO UPDATE SET version = excluded.version;\n"
    )
    ok, problems = tv.validate_migration_additive(additive)
    assert ok is True, problems


def test_validator_flags_statement_head_update():
    ok, problems = tv.validate_migration_additive("UPDATE events SET version = '';")
    assert ok is False
    assert any("UPDATE" in p for p in problems)


def test_validator_flags_content_column():
    fake_schema = (
        "CREATE TABLE events (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  daily_id TEXT NOT NULL,\n"
        "  summary TEXT NOT NULL DEFAULT ''\n"  # content column — must be flagged
        ");"
    )
    cols = tv.parse_schema_columns(fake_schema)
    assert "summary" in tv._content_markers_in(cols)


def test_validator_reports_missing_worker_dir(tmp_path):
    report = tv.validate_telemetry_contract(tmp_path)
    assert report["ok"] is False
    assert any("schema.sql" in p for p in report["problems"])


# --- opt-in + no-content runtime guards --------------------------------------

@pytest.fixture
def telem_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
    monkeypatch.delenv("ENGRAM_TELEMETRY_REMOTE", raising=False)
    return tmp_path


def test_build_payload_returns_none_when_disabled(telem_env):
    # Default: telemetry off → no payload built (opt-in).
    assert telemetry.is_enabled() is False
    assert telemetry.build_payload(engram_version="3.45.3") is None


def test_remote_requires_local_and_remote_consent(telem_env):
    telemetry.set_enabled(True)
    # Local on, remote not consented → remote stays off.
    assert telemetry.is_remote_enabled() is False
    telemetry.set_remote_enabled(True)
    assert telemetry.is_remote_enabled() is True


def test_enabled_payload_only_emits_declared_contract_fields(telem_env):
    telemetry.set_enabled(True)
    payload = telemetry.build_payload(
        engram_version="3.45.3",
        tool_calls={"get_user_context": {"success": 2, "error": 0}},
        knowledge_counts={"lessons": 10, "decisions": 3, "domains": 4},
    )
    assert payload is not None
    allowed = set(tv.PAYLOAD_TO_COLUMN) | tv.TRANSPORT_ONLY_KEYS
    extra = set(payload) - allowed
    assert extra == set(), f"payload emitted undeclared field(s): {extra}"


def test_validate_payload_rejects_oversized_value():
    # A value over the max field length (conversation content shape) is rejected.
    bad = {"daily_id": "abc", "leaked": "x" * 250}
    assert telemetry._validate_payload(bad) is False


def test_validate_payload_rejects_high_space_ratio_text():
    # A clearly natural-language value (>100 chars, >20% spaces) is rejected.
    sentence = ("a " * 80).strip()  # ~160 chars, ~50% spaces
    assert telemetry._validate_payload({"daily_id": "abc", "leaked": sentence}) is False


def test_validate_payload_rejects_pathlike_key():
    assert telemetry._validate_payload({"a/b/c": 1}) is False
