"""R1-1: _now_iso() must emit UTC timestamps, not naive local time.

Bug: storage._now_iso() uses datetime.now() (naive local). In UTC+8,
provenance._parse_iso() reads this as UTC, making entries appear 8h in
the future — flagged stale/clock_skewed within hours.

Fix: _now_iso() → datetime.now(timezone.utc) with Z suffix.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from piia_engram.storage import _now_iso, _parse_iso


class TestNowIsoUtc:
    def test_ends_with_z(self):
        ts = _now_iso()
        assert ts.endswith("Z"), f"Expected Z suffix, got: {ts}"

    def test_no_offset_notation(self):
        ts = _now_iso()
        assert "+00:00" not in ts
        assert "+08:00" not in ts

    def test_close_to_utc_now(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        ts = _now_iso()
        after = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert before <= parsed <= after, f"{ts} not within UTC window [{before}, {after}]"

    def test_no_microseconds(self):
        ts = _now_iso()
        assert "." not in ts, f"Microseconds present: {ts}"


class TestParseIsoBackwardCompat:
    def test_parses_z_suffix(self):
        result = _parse_iso("2026-06-23T06:00:00Z")
        assert result is not None
        assert result == datetime(2026, 6, 23, 6, 0, 0)

    def test_parses_old_naive_timestamp(self):
        result = _parse_iso("2026-06-23T14:00:00")
        assert result is not None
        assert result == datetime(2026, 6, 23, 14, 0, 0)

    def test_parses_offset_timestamp(self):
        result = _parse_iso("2026-06-23T14:00:00+08:00")
        assert result is not None
        assert result == datetime(2026, 6, 23, 6, 0, 0)

    def test_none_returns_none(self):
        assert _parse_iso(None) is None
        assert _parse_iso("") is None

    def test_invalid_returns_none(self):
        assert _parse_iso("not-a-date") is None

    def test_returns_naive_datetime(self):
        """_parse_iso returns naive (no tzinfo) for comparison consistency."""
        result = _parse_iso("2026-06-23T06:00:00Z")
        assert result is not None
        assert result.tzinfo is None

    def test_roundtrip_now_iso(self):
        ts = _now_iso()
        parsed = _parse_iso(ts)
        assert parsed is not None
        utc_now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
        assert abs((parsed - utc_now).total_seconds()) < 2
