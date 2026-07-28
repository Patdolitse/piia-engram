"""Local static smoke harness for the telemetry worker (no Cloudflare, no JS run).

Simulates the worker's behaviour by reading ``worker/src/index.js`` and asserting
the structure of the three insert tiers and the dashboard labels, so the
remote-rollout-critical paths are pinned without standing up a D1 / wrangler:

- v1.1 schema full insert path (P0 + P1 columns)
- v1 schema fallback (P1 columns missing)
- legacy fallback (P0 columns also missing)
- rejected content fields (the allowlist gate is present and content-free)
- dashboard label checks (anonymous daily-id wording + v1.1 tiles)

A *dynamic* counterpart that actually executes ``handleEvent`` against a mock D1
lives in ``worker/test/smoke.mjs`` (node-only, not part of the pytest suite so
CI stays dependency-light). This module is the always-on guard.
"""

from __future__ import annotations

import re
from pathlib import Path

from piia_engram import telemetry_validation as tv

WORKER = Path(__file__).resolve().parents[1] / "worker"
INDEX = WORKER / "src" / "index.js"


def _source() -> str:
    return INDEX.read_text(encoding="utf-8")


def _event_insert_column_sets(source: str) -> list[list[str]]:
    """Return the column lists of every ``INSERT INTO events (...)`` statement,
    in source order (full v1.1, then v1/P0, then legacy)."""
    sets: list[list[str]] = []
    for m in re.finditer(r"INSERT\s+INTO\s+events\s*\(([^)]*)\)", source, re.IGNORECASE):
        cols = [c.strip() for c in m.group(1).split(",") if c.strip()]
        sets.append(cols)
    return sets


# --- the three insert tiers -------------------------------------------------

def test_three_event_insert_tiers_present():
    sets = _event_insert_column_sets(_source())
    assert len(sets) == 3, f"expected 3 event insert tiers, found {len(sets)}"


def test_full_v1_1_insert_carries_all_columns():
    full, p0, legacy = _event_insert_column_sets(_source())
    full_set = set(full)
    # P0 + P1 columns all present on the full path.
    assert tv.V1_P0_COLUMNS.issubset(full_set)
    assert tv.V1_1_DERIVED_COLUMNS.issubset(full_set)


def test_v1_fallback_drops_p1_but_keeps_p0():
    full, p0, legacy = _event_insert_column_sets(_source())
    p0_set = set(p0)
    # The v1/P0 fallback keeps the P0 columns…
    assert tv.V1_P0_COLUMNS.issubset(p0_set)
    # …and drops every P1 derived bucket.
    assert not (tv.V1_1_DERIVED_COLUMNS & p0_set)


def test_legacy_fallback_drops_p0_and_p1():
    full, p0, legacy = _event_insert_column_sets(_source())
    legacy_set = set(legacy)
    assert not (tv.V1_P0_COLUMNS & legacy_set)
    assert not (tv.V1_1_DERIVED_COLUMNS & legacy_set)
    # base columns survive
    assert {"daily_id", "version", "tool_calls", "os", "py", "tier"}.issubset(legacy_set)


def test_fallback_dispatch_references_column_gates():
    source = _source()
    # The worker decides whether to fall back by inspecting the D1 error for the
    # missing column name; both gate sets must be referenced.
    assert "P1_COLS" in source and "P0_COLS" in source
    assert "mentionsAny" in source


def test_no_content_columns_in_any_insert_tier():
    for cols in _event_insert_column_sets(_source()):
        assert tv._content_markers_in(set(cols)) == [], cols


# --- rejected content fields ------------------------------------------------

def test_event_allowlist_gate_rejects_unexpected_fields():
    source = _source()
    # The validator rejects any field outside ALLOWED_FIELDS.
    assert "unexpected field" in source
    allow = tv.parse_js_string_set(source, "ALLOWED_FIELDS")
    # A content-shaped field name is NOT on the allowlist (would be rejected),
    # and the allowlist itself carries no content marker.
    assert "summary" not in allow
    assert tv._content_markers_in(allow) == []


# --- dashboard labels -------------------------------------------------------

def test_dashboard_has_v1_1_tiles_and_anonymous_wording():
    ok, problems = tv.validate_dashboard_wording(_source())
    assert ok, problems


def test_dashboard_pypi_downloads_have_range_selector():
    source = _source()
    for key in ("7d", "14d", "30d", "month", "quarter", "year"):
        assert f'data-download-range="{key}"' in source
    assert "downloadRangeRows" in source
    assert "setDownloadRange" in source


def test_dashboard_pypi_downloads_use_single_card_kpi_layout():
    source = _source()
    assert "pypi-card" in source
    assert "pypi-kpis" in source
    assert "download-current-total" in source
    assert "data-download-total" in source
    assert "当前区间总下载" in source
    assert "近 7 天下载（PyPI API）" in source
    assert "近 30 天下载（PyPI API）" in source
    assert "兼容口径" not in source


def test_dashboard_pypi_chart_uses_sparse_scrollable_labels():
    source = _source()
    assert "bar-scroll" in source
    assert "bar-peak" in source
    assert "labelStep" in source
    assert "showLabel" in source
    assert "title=\"${title}\"" in source
    assert "aria-label=\"${title}\"" in source
    assert "bar-val" not in source


def test_dashboard_activity_trends_have_range_selector():
    source = _source()
    for key in ("7d", "14d", "30d", "month", "quarter", "year"):
        assert f'data-activity-range="{key}"' in source
    assert "activityRangeRows" in source
    assert "setActivityRange" in source


def test_worker_exposes_recent_anonymous_activity_windows():
    source = _source()
    assert "'/v1/active'" in source
    assert "recent_active" in source
    assert "anonymous_daily_id_activity" in source
    assert "active_install_estimate" in source
    assert "anonymous_install_days" in source
    assert "anonymous_daily_ids" in source
    assert "only today approximates active installs" in source


def test_dashboard_v1_1_section_gated_on_migration():
    source = _source()
    # The v1.1 tiles only render real numbers once the migration is applied;
    # otherwise an explicit placeholder is shown (no fabricated counts).
    assert "hasAnalysisContractV1_1" in source
    assert "尚未应用 Telemetry Analysis Contract v1.1 迁移" in source
