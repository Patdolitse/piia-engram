"""Telemetry Analysis Contract v1.1 — P1 derived-bucket fields.

Asserts P0 fields are preserved, the new P1 buckets are present + privacy-safe
(short bucket strings, pass the payload validator, leak no timestamps), and the
worker draft + schema + migration reference the new columns.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from piia_engram.telemetry import (
    CONTRACT_VERSION,
    ToolCallTracker,
    _compute_vnext_signals,
    _validate_payload,
    build_payload,
    preview_payload,
    set_enabled,
)


@pytest.fixture(autouse=True)
def isolated_engram_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.delenv("ENGRAM_TELEMETRY", raising=False)
    monkeypatch.delenv("ENGRAM_TELEMETRY_REMOTE", raising=False)
    monkeypatch.delenv("ENGRAM_TELEMETRY_URL", raising=False)
    return tmp_path


def _set_cfg(root: Path, **kw):
    cfg_path = root / "telemetry_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.update(kw)
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")


class TestP0Preserved:
    def test_all_p0_fields_still_present(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(engram_version="3.45.3")
        for field in ("prev_version", "session_type", "install_age_bucket",
                      "error_categories"):
            assert field in payload


class TestP1Fields:
    def test_contract_version_and_buckets_present(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(engram_version="3.45.3")
        assert payload["contract_version"] == CONTRACT_VERSION == "1.1"
        assert payload["schema"] == 1  # transport schema unchanged
        for field in ("version_adoption", "activation_state",
                      "returning_bucket", "error_trend"):
            assert field in payload

    def test_version_adoption_first_then_upgrade_downgrade(self, isolated_engram_dir):
        set_enabled(True)
        # first run, no prev
        assert build_payload(engram_version="3.45.3")["version_adoption"] == "first"
        _set_cfg(isolated_engram_dir, last_engram_version="3.45.2")
        assert build_payload(engram_version="3.45.3")["version_adoption"] == "upgrade"
        assert build_payload(engram_version="3.45.1")["version_adoption"] == "downgrade"
        assert build_payload(engram_version="3.45.2")["version_adoption"] == "same"

    def test_version_adoption_equal_length_padding(self, isolated_engram_dir):
        """Mixed-length versions compare by value, not tuple length (3.1 == 3.1.0)."""
        set_enabled(True)
        _set_cfg(isolated_engram_dir, last_engram_version="3.1")
        assert build_payload(engram_version="3.1.0")["version_adoption"] == "same"
        _set_cfg(isolated_engram_dir, last_engram_version="3.1.0")
        assert build_payload(engram_version="3.2")["version_adoption"] == "upgrade"

    def test_activation_state(self, isolated_engram_dir):
        set_enabled(True)
        assert build_payload(engram_version="3.45.3")["activation_state"] == "unknown"
        on = build_payload(engram_version="3.45.3",
                           knowledge_counts={"lessons": 3, "decisions": 1})
        assert on["activation_state"] == "activated"
        off = build_payload(engram_version="3.45.3",
                            knowledge_counts={"lessons": 0, "decisions": 0})
        assert off["activation_state"] == "not_activated"

    def test_returning_bucket(self, isolated_engram_dir):
        set_enabled(True)
        assert build_payload(engram_version="3.45.3")["returning_bucket"] == "new"
        _set_cfg(isolated_engram_dir, first_payload_sent_at="2026-06-01T00:00:00+00:00")
        assert build_payload(engram_version="3.45.3")["returning_bucket"] == "returning"

    def test_error_trend_none_when_no_errors(self, isolated_engram_dir):
        set_enabled(True)
        assert build_payload(engram_version="3.45.3")["error_trend"] == "none"

    def test_error_trend_tracks_across_flushes(self, isolated_engram_dir):
        set_enabled(True)
        tracker = ToolCallTracker()
        tracker.record("add_lesson", success=False, error_category="timeout")
        tracker.record("add_lesson", success=False, error_category="io")
        r1 = tracker.flush(engram_version="3.45.3", force=True)
        first = json.loads(r1.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert first["error_trend"] == "first"  # no prior total stored
        # config now records last_error_total = 2
        cfg = json.loads((isolated_engram_dir / "telemetry_config.json").read_text("utf-8"))
        assert cfg["last_error_total"] == 2

        tracker.record("add_lesson", success=False, error_category="timeout")
        r2 = tracker.flush(engram_version="3.45.3", force=True)
        second = json.loads(r2.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert second["error_trend"] == "down"  # 1 < 2


class TestPrivacyShape:
    def test_payload_passes_validator(self, isolated_engram_dir):
        set_enabled(True)
        _set_cfg(isolated_engram_dir, last_engram_version="3.45.2",
                 first_payload_sent_at="2026-06-01T00:00:00+00:00",
                 last_error_total=1)
        payload = build_payload(engram_version="3.45.3",
                                knowledge_counts={"lessons": 2, "decisions": 1})
        assert _validate_payload(payload) is True

    def test_p1_values_are_short_buckets_no_timestamps(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(engram_version="3.45.3")
        for field in ("version_adoption", "activation_state",
                      "returning_bucket", "error_trend", "contract_version"):
            value = payload[field]
            assert isinstance(value, str) and len(value) <= 16
        # No granular install/opt-in timestamps leak via the new fields.
        blob = json.dumps(payload)
        assert "first_seen_at" not in blob
        assert "last_error_total" not in blob

    def test_preview_includes_v1_1_fields(self, isolated_engram_dir):
        text = preview_payload(engram_version="3.45.3")
        assert "contract_version" in text
        assert "version_adoption" in text


class TestVNextSignals:
    def test_vnext_signal_computation_from_aggregate_counts(self):
        signals = _compute_vnext_signals({
            "get_resume_brief": {"success": 4, "error": 1},
            "get_resume_brief_nonempty": {"success": 3, "error": 0},
            "wrap_up_session": {"success": 2, "error": 0},
        })

        assert signals == {
            "recall_hit_rate": 0.75,
            "cross_tool_handoffs": 2,
        }

    def test_vnext_recall_hit_rate_unknown_without_nonempty_counter(self):
        signals = _compute_vnext_signals({
            "get_resume_brief": {"success": 4, "error": 0},
            "wrap_up_session": {"success": 1, "error": 0},
        })

        assert signals["recall_hit_rate"] is None
        assert signals["cross_tool_handoffs"] == 1

    def test_vnext_counts_are_clamped_and_do_not_exceed_resume_success(self):
        signals = _compute_vnext_signals({
            "get_resume_brief": {"success": 2, "error": 0},
            "get_resume_brief_nonempty": {"success": 9, "error": 0},
            "wrap_up_session": {"success": "7", "error": 0},
        })

        assert signals["recall_hit_rate"] == 1.0
        assert signals["cross_tool_handoffs"] == 2

    def test_vnext_signals_default_off(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(
            engram_version="3.49.1",
            tool_calls={"get_resume_brief": {"success": 1, "error": 0}},
        )

        assert "vnext_signals" not in payload

    def test_vnext_signals_opt_in_and_validator_safe(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(
            engram_version="3.49.1",
            tool_calls={
                "get_resume_brief": {"success": 2, "error": 0},
                "get_resume_brief_nonempty": {"success": 1, "error": 0},
                "wrap_up_session": {"success": 1, "error": 0},
            },
            include_vnext_signals=True,
        )

        assert payload["vnext_signals"] == {
            "recall_hit_rate": 0.5,
            "cross_tool_handoffs": 1,
        }
        assert _validate_payload(payload) is True

    def test_vnext_signals_do_not_add_content_fields(self, isolated_engram_dir):
        set_enabled(True)
        payload = build_payload(
            engram_version="3.49.1",
            tool_calls={
                "get_resume_brief": {"success": 1, "error": 0},
                "get_resume_brief_nonempty": {"success": 1, "error": 0},
            },
            include_vnext_signals=True,
        )
        blob = json.dumps(payload, ensure_ascii=False)

        assert "prompt" not in blob
        assert "path" not in blob
        assert "session_id" not in blob


WORKER_INDEX = Path(__file__).resolve().parents[1] / "worker" / "src" / "index.js"
WORKER_SCHEMA = Path(__file__).resolve().parents[1] / "worker" / "schema.sql"
MIGRATION = (Path(__file__).resolve().parents[1] / "worker" / "migrations"
             / "20260603_telemetry_contract_v1_1.sql")

_P1_COLUMNS = ("contract_version", "version_adoption", "activation_state",
               "returning_bucket", "error_trend")


class TestWorkerDraftContract:
    def test_worker_source_and_schema_reference_p1_fields(self):
        source = WORKER_INDEX.read_text(encoding="utf-8")
        schema = WORKER_SCHEMA.read_text(encoding="utf-8")
        for field in _P1_COLUMNS:
            assert f"'{field}'" in source, f"worker source missing {field}"
            assert field in schema, f"schema missing {field}"

    def test_worker_still_references_p0_fields(self):
        source = WORKER_INDEX.read_text(encoding="utf-8")
        for field in ("prev_version", "session_type", "install_age_bucket",
                      "error_categories"):
            assert f"'{field}'" in source

    def test_migration_adds_all_p1_columns(self):
        assert MIGRATION.is_file()
        sql = MIGRATION.read_text(encoding="utf-8")
        for field in _P1_COLUMNS:
            assert f"ADD COLUMN {field}" in sql

    def test_dashboard_copy_unchanged_daily_id_wording(self):
        source = WORKER_INDEX.read_text(encoding="utf-8")
        assert "匿名日 ID" in source
        assert "独立用户" not in source
        assert "用户数" not in source
