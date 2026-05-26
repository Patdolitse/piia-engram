"""Tests for beta_tracker — governance lifecycle event instrumentation."""

import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_beta(tmp_path, monkeypatch):
    """Isolate beta tracker to a temp directory."""
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    # Reset module-level caches
    import piia_engram.beta_tracker as bt
    monkeypatch.setattr(bt, "_engram_root", lambda: tmp_path)


class TestTrackEvent:
    """track_event writes events to beta_events.jsonl."""

    def test_basic_event(self, tmp_path):
        from piia_engram.beta_tracker import track_event, _events_path
        track_event("cold_start", level="quick")
        path = _events_path()
        assert path.is_file()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        ev = json.loads(lines[0])
        assert ev["event"] == "cold_start"
        assert "ts" in ev
        assert ev["d"]["level"] == "quick"

    def test_multiple_events(self, tmp_path):
        from piia_engram.beta_tracker import track_event, _events_path
        track_event("cold_start", level="standard")
        track_event("knowledge_created", kind="lesson", domain="python")
        track_event("session_end", source_tool="claude_code")
        path = _events_path()
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_no_data_event(self, tmp_path):
        from piia_engram.beta_tracker import track_event, _events_path
        track_event("knowledge_reviewed")
        path = _events_path()
        ev = json.loads(path.read_text(encoding="utf-8").strip())
        assert ev["event"] == "knowledge_reviewed"
        assert "d" not in ev  # no data attached

    def test_long_strings_truncated(self, tmp_path):
        from piia_engram.beta_tracker import track_event, _events_path
        track_event("test", long_value="x" * 200)
        path = _events_path()
        ev = json.loads(path.read_text(encoding="utf-8").strip())
        # String > 100 chars should be dropped (not truncated, just excluded)
        assert "d" not in ev or "long_value" not in ev.get("d", {})

    def test_disabled_via_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_BETA_TRACKING", "0")
        from piia_engram.beta_tracker import track_event, _events_path
        track_event("cold_start", level="quick")
        path = _events_path()
        assert not path.exists()

    def test_never_raises(self, tmp_path, monkeypatch):
        """track_event must never raise, even with broken filesystem."""
        import piia_engram.beta_tracker as bt
        monkeypatch.setattr(bt, "_events_path",
                            lambda: Path("/nonexistent/dir/events.jsonl"))
        # Should not raise
        bt.track_event("test_event", key="value")

    def test_no_content_leakage(self, tmp_path):
        """Only short strings and primitives are written — no content."""
        from piia_engram.beta_tracker import track_event, _events_path
        track_event("knowledge_created",
                    kind="lesson",
                    domain="python",
                    # These should be excluded (too long or wrong type)
                    summary="This is a long lesson about how to do things " * 5,
                    content={"nested": "dict"})
        path = _events_path()
        ev = json.loads(path.read_text(encoding="utf-8").strip())
        d = ev.get("d", {})
        assert "summary" not in d  # too long
        assert "content" not in d  # dict not allowed
        assert d.get("kind") == "lesson"  # short string OK
        assert d.get("domain") == "python"  # short string OK


class TestReadEvents:
    """read_events returns parsed events list."""

    def test_empty_dir(self, tmp_path):
        from piia_engram.beta_tracker import read_events
        assert read_events() == []

    def test_reads_all_events(self, tmp_path):
        from piia_engram.beta_tracker import track_event, read_events
        track_event("a")
        track_event("b")
        track_event("c")
        events = read_events()
        assert len(events) == 3
        assert [e["event"] for e in events] == ["a", "b", "c"]

    def test_handles_corrupted_lines(self, tmp_path):
        from piia_engram.beta_tracker import _events_path, read_events
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"event":"good","ts":"2026-01-01"}\nBAD LINE\n{"event":"ok","ts":"2026-01-02"}\n',
                        encoding="utf-8")
        events = read_events()
        assert len(events) == 2


class TestAggregateEvents:
    """aggregate_events produces governance summary from events."""

    def test_empty(self, tmp_path):
        from piia_engram.beta_tracker import aggregate_events
        assert aggregate_events() == {}

    def test_counts_events(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("cold_start", level="quick")
        track_event("cold_start", level="standard")
        track_event("knowledge_created", kind="lesson")
        track_event("session_end")
        result = aggregate_events()
        assert result["total_events"] == 4
        assert result["event_counts"]["cold_start"] == 2
        assert result["event_counts"]["knowledge_created"] == 1
        assert result["event_counts"]["session_end"] == 1

    def test_cold_start_levels(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("cold_start", level="quick")
        track_event("cold_start", level="quick")
        track_event("cold_start", level="standard")
        result = aggregate_events()
        assert result["cold_starts"] == {"quick": 2, "standard": 1}

    def test_knowledge_created_by_tool(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("knowledge_created", kind="lesson", source_tool="claude_code")
        track_event("knowledge_created", kind="decision", source_tool="cursor")
        track_event("knowledge_created", kind="lesson", source_tool="claude_code")
        result = aggregate_events()
        assert result["created_by_tool"] == {"claude_code": 2, "cursor": 1}

    def test_promotion_tracking(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("knowledge_promoted", count=3, method="auto_access")
        track_event("knowledge_promoted", count=1, method="manual")
        result = aggregate_events()
        assert result["promotions"]["total"] == 4
        assert result["promotions"]["methods"] == {"auto_access": 3, "manual": 1}

    def test_reconcile_tracking(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("reconcile", imported=5)
        track_event("reconcile", imported=2)
        result = aggregate_events()
        assert result["reconcile"]["sync_count"] == 2
        assert result["reconcile"]["total_imported"] == 7

    def test_sessions_by_tool(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("session_end", source_tool="claude_code")
        track_event("session_end", source_tool="codex")
        track_event("session_end", source_tool="claude_code")
        result = aggregate_events()
        assert result["sessions_by_tool"] == {"claude_code": 2, "codex": 1}

    def test_domain_distribution(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("knowledge_created", kind="lesson", domain="python,testing")
        track_event("knowledge_created", kind="lesson", domain="python")
        result = aggregate_events()
        assert result["created_by_domain"]["python"] == 2
        assert result["created_by_domain"]["testing"] == 1

    def test_created_tiers(self, tmp_path):
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("knowledge_created", kind="lesson", tier="staging")
        track_event("knowledge_created", kind="lesson", tier="staging")
        track_event("knowledge_created", kind="decision", tier="verified")
        result = aggregate_events()
        assert result["created_tiers"] == {"staging": 2, "verified": 1}

    def test_no_content_in_aggregate(self, tmp_path):
        """Aggregate output contains no knowledge content."""
        from piia_engram.beta_tracker import track_event, aggregate_events
        track_event("knowledge_created", kind="lesson", domain="python")
        result = aggregate_events()
        result_str = json.dumps(result)
        # Should not contain any natural-language content
        assert "summary" not in result_str
        assert "detail" not in result_str
        assert "content" not in result_str
