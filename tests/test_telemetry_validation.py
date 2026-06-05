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


# --- L3: telemetry contract drift guard (payload/schema/migration/worker) -----

def _worker_js() -> str:
    return (WORKER / "src" / "index.js").read_text(encoding="utf-8")


def test_worker_event_allowlist_matches_client_contract():
    allow = tv.parse_js_string_set(_worker_js(), "ALLOWED_FIELDS")
    expected = set(tv.PAYLOAD_TO_COLUMN) | tv.TRANSPORT_ONLY_KEYS
    assert allow == expected, {"missing": expected - allow, "extra": allow - expected}


def test_worker_feedback_allowlist_matches_python_allowlist():
    allow = tv.parse_js_string_set(_worker_js(), "FEEDBACK_ALLOWED_FIELDS")
    assert allow == set(tv.FEEDBACK_ALLOWED_KEYS), {
        "missing": set(tv.FEEDBACK_ALLOWED_KEYS) - allow,
        "extra": allow - set(tv.FEEDBACK_ALLOWED_KEYS),
    }


def test_no_content_field_in_worker_allowlists():
    js = _worker_js()
    allow = (tv.parse_js_string_set(js, "ALLOWED_FIELDS")
             | tv.parse_js_string_set(js, "FEEDBACK_ALLOWED_FIELDS"))
    assert tv._content_markers_in(allow) == []


def test_full_contract_validation_surfaces_worker_allowlists():
    report = tv.validate_telemetry_contract(WORKER)
    assert report["ok"], report["problems"]
    # The richer report now exposes the parsed worker allowlists.
    assert report["worker_event_allowlist"]
    assert report["worker_feedback_allowlist"]


# The guard must FAIL on a planted content-like field in any of the four
# surfaces. We exercise the underlying detectors the contract check composes.

def test_guard_catches_planted_content_in_payload_allowlist():
    planted = set(tv.PAYLOAD_TO_COLUMN) | {"prompt_text"}
    assert tv._content_markers_in(planted)  # non-empty ⇒ flagged


def test_guard_catches_planted_content_in_migration_added_columns():
    planted_migration = (
        "ALTER TABLE events ADD COLUMN install_age_bucket TEXT;\n"
        "ALTER TABLE events ADD COLUMN summary_body TEXT;\n"  # content — must flag
    )
    added = tv.parse_added_columns(planted_migration)
    assert "summary_body" in added
    assert tv._content_markers_in(added)


def test_guard_catches_planted_content_in_worker_allowlist():
    planted_js = (
        "const ALLOWED_FIELDS = new Set([\n"
        "  'daily_id', 'engram_version', 'message_body',\n"  # content — must flag
        "]);"
    )
    allow = tv.parse_js_string_set(planted_js, "ALLOWED_FIELDS")
    assert "message_body" in allow
    assert tv._content_markers_in(allow)


def test_contract_validation_flags_drifted_worker_allowlist(tmp_path):
    # A worker tree whose event allowlist drops a contract field must be flagged.
    (tmp_path / "src").mkdir()
    (tmp_path / "migrations").mkdir()
    # Minimal but valid schema + v1.1 migration so only the drift trips.
    cols = ", ".join(f"{c} TEXT" for c in sorted(
        set(tv.PAYLOAD_TO_COLUMN.values()) | tv.V1_1_DERIVED_COLUMNS))
    (tmp_path / "schema.sql").write_text(
        f"CREATE TABLE events ({cols});", encoding="utf-8")
    add_cols = "\n".join(f"ALTER TABLE events ADD COLUMN {c} TEXT;"
                         for c in sorted(tv.V1_1_DERIVED_COLUMNS))
    (tmp_path / "migrations" / "20260603_telemetry_contract_v1_1.sql").write_text(
        add_cols, encoding="utf-8")
    # Drifted worker: missing 'timestamp' from the event allowlist.
    drifted = set(tv.PAYLOAD_TO_COLUMN)  # note: no 'timestamp'
    fb = ", ".join(f"'{k}'" for k in sorted(tv.FEEDBACK_ALLOWED_KEYS))
    ev = ", ".join(f"'{k}'" for k in sorted(drifted))
    (tmp_path / "src" / "index.js").write_text(
        f"const ALLOWED_FIELDS = new Set([{ev}]);\n"
        f"const FEEDBACK_ALLOWED_FIELDS = new Set([{fb}]);\n",
        encoding="utf-8")
    report = tv.validate_telemetry_contract(tmp_path)
    assert report["ok"] is False
    assert any("worker event allowlist drift" in p for p in report["problems"])


# --- remote-readiness report (pure/local/read-only) --------------------------

TELEMETRY_SRC = ROOT / "src" / "piia_engram" / "telemetry.py"


def test_real_remote_readiness_is_green():
    report = tv.validate_remote_readiness(WORKER)
    assert report["ok"], report["problems"]
    names = {c["name"] for c in report["checks"]}
    # Every required dimension is reported.
    assert names == {
        "client_payload_fields", "schema_columns",
        "worker_event_allowlist", "worker_feedback_allowlist",
        "migration_files", "migration_sequencing",
        "dashboard_wording", "optin_defaults", "no_content_fields",
    }
    assert all(c["ok"] for c in report["checks"])


def test_render_readiness_text_smoke():
    report = tv.validate_remote_readiness(WORKER)
    text = tv.render_readiness_text(report)
    assert "READY" in text
    assert "migration_sequencing" in text


def test_readiness_reports_blockers_for_empty_worker(tmp_path):
    report = tv.validate_remote_readiness(tmp_path)
    assert report["ok"] is False
    assert report["problems"]
    # Still produces every named check even when files are absent.
    assert len(report["checks"]) == 9


def test_parse_added_columns_ignores_commented_prose():
    # A comment mentioning "ADD COLUMN is ..." must not register a phantom column.
    sql = (
        "-- NOTE: ADD COLUMN is NOT idempotent on re-run.\n"
        "ALTER TABLE events ADD COLUMN version_adoption TEXT NOT NULL DEFAULT '';\n"
    )
    assert tv.parse_added_columns(sql) == {"version_adoption"}


def test_real_v1_1_migration_adds_exactly_five_columns():
    mig = (WORKER / "migrations" / tv.V1_1_MIGRATION_NAME).read_text(encoding="utf-8")
    assert tv.parse_added_columns(mig) == set(tv.V1_1_DERIVED_COLUMNS)


def test_optin_defaults_pass_on_real_client():
    ok, problems = tv.validate_optin_defaults(TELEMETRY_SRC.read_text(encoding="utf-8"))
    assert ok, problems


def test_optin_defaults_flag_an_opt_out_to_opt_in_flip():
    # Simulate a client that defaults telemetry ON — must be flagged.
    flipped = TELEMETRY_SRC.read_text(encoding="utf-8").replace(
        'get("enabled", False)', 'get("enabled", True)')
    ok, problems = tv.validate_optin_defaults(flipped)
    assert ok is False
    assert any("opt-out" in p for p in problems)


def test_dashboard_wording_flags_unique_person_claim():
    fake = "匿名日 ID daily_id 按 UTC 日期轮换 版本采纳 知识激活 匿名回访分桶 错误趋势 独立用户"
    ok, problems = tv.validate_dashboard_wording(fake)
    assert ok is False
    assert any("unique-person" in p for p in problems)


def test_dashboard_wording_flags_missing_v1_1_tile():
    fake = "匿名日 ID daily_id 按 UTC 日期轮换 版本采纳 知识激活 错误趋势"  # no 匿名回访分桶
    ok, problems = tv.validate_dashboard_wording(fake)
    assert ok is False
    assert any("匿名回访分桶" in p for p in problems)


def test_dashboard_wording_flags_missing_vnext_local_tile():
    fake = (
        "匿名日 ID daily_id 按 UTC 日期轮换 版本采纳 知识激活 "
        "匿名回访分桶 错误趋势"
    )
    ok, problems = tv.validate_dashboard_wording(fake)
    assert ok is False
    assert any("vNext 本地信号" in p for p in problems)


def test_dashboard_wording_passes_with_vnext_local_copy():
    fake = (
        "匿名日 ID daily_id 按 UTC 日期轮换 版本采纳 知识激活 "
        "匿名回访分桶 错误趋势 vNext 本地信号 默认关闭 / 仅本地 / 未写入远程 D1"
    )
    ok, problems = tv.validate_dashboard_wording(fake)
    assert ok is True, problems


def test_dashboard_vnext_labels_stay_out_of_remote_contract():
    labels = set(tv.DASHBOARD_VNEXT_LOCAL_LABELS)
    assert labels.isdisjoint(tv.PAYLOAD_TO_COLUMN)
    assert labels.isdisjoint(tv.V1_1_DERIVED_COLUMNS)


def test_readiness_flags_migration_sequencing_overlap(tmp_path):
    # A v1.1 migration that re-adds a P0 column would fail when applied after v1.
    (tmp_path / "src").mkdir()
    (tmp_path / "migrations").mkdir()
    cols = ", ".join(f"{c} TEXT" for c in sorted(
        set(tv.PAYLOAD_TO_COLUMN.values()) | tv.V1_1_DERIVED_COLUMNS | tv.V1_P0_COLUMNS))
    (tmp_path / "schema.sql").write_text(f"CREATE TABLE events ({cols});", encoding="utf-8")
    (tmp_path / "migrations" / tv.V1_MIGRATION_NAME).write_text(
        "\n".join(f"ALTER TABLE events ADD COLUMN {c} TEXT;" for c in sorted(tv.V1_P0_COLUMNS)),
        encoding="utf-8")
    # v1.1 wrongly re-adds a P0 column (prev_version) alongside the derived buckets.
    bad = sorted(tv.V1_1_DERIVED_COLUMNS) + ["prev_version"]
    (tmp_path / "migrations" / tv.V1_1_MIGRATION_NAME).write_text(
        "\n".join(f"ALTER TABLE events ADD COLUMN {c} TEXT;" for c in bad), encoding="utf-8")
    ev = ", ".join(f"'{k}'" for k in sorted(set(tv.PAYLOAD_TO_COLUMN) | tv.TRANSPORT_ONLY_KEYS))
    fb = ", ".join(f"'{k}'" for k in sorted(tv.FEEDBACK_ALLOWED_KEYS))
    (tmp_path / "src" / "index.js").write_text(
        f"const ALLOWED_FIELDS = new Set([{ev}]);\n"
        f"const FEEDBACK_ALLOWED_FIELDS = new Set([{fb}]);\n", encoding="utf-8")
    report = tv.validate_remote_readiness(tmp_path)
    seq = next(c for c in report["checks"] if c["name"] == "migration_sequencing")
    assert seq["ok"] is False
    assert any("re-adds P0" in p for p in seq["problems"])
