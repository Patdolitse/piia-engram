"""Tests for the PostCompact hook: auto_absorb_compact.py (v3.30 R4)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _extract_compact_summary unit tests
# ---------------------------------------------------------------------------


class TestExtractCompactSummary:
    """The extractor must find ≥200 char text blocks in the transcript head."""

    def test_extracts_text_block_from_content_list(self, tmp_path: Path):
        """Content is a list of typed blocks — standard Claude transcript format."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        summary_text = "A" * 300  # 300 chars
        transcript = tmp_path / "transcript.jsonl"
        entry = {"type": "assistant", "content": [{"type": "text", "text": summary_text}]}
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == summary_text

    def test_extracts_plain_string_content(self, tmp_path: Path):
        """Content can also be a plain string (older transcript format)."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        summary_text = "B" * 250
        transcript = tmp_path / "transcript.jsonl"
        entry = {"type": "system", "content": summary_text}
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == summary_text

    def test_skips_short_entries(self, tmp_path: Path):
        """Entries <200 chars are not compact summaries — skip them."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "system", "content": "short entry"}),
            json.dumps({"type": "assistant", "content": [{"type": "text", "text": "also short"}]}),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == ""

    def test_returns_first_qualifying_entry(self, tmp_path: Path):
        """If multiple long entries exist, take the first one."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        first_summary = "FIRST" * 60   # 300 chars
        second_summary = "SECOND" * 60  # 360 chars
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "system", "content": first_summary}),
            json.dumps({"type": "assistant", "content": second_summary}),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == first_summary

    def test_handles_nonexistent_transcript(self):
        """Non-existent path should return empty string, not crash."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        result = _extract_compact_summary("/nonexistent/path/transcript.jsonl")
        assert result == ""

    def test_handles_empty_file(self, tmp_path: Path):
        """Empty transcript → empty string."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == ""

    def test_handles_invalid_json_lines(self, tmp_path: Path):
        """Malformed JSONL lines are skipped gracefully."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        valid_summary = "C" * 250
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            "not json at all",
            json.dumps({"type": "assistant", "content": valid_summary}),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == valid_summary

    def test_only_scans_first_10_lines(self, tmp_path: Path):
        """The scanner should stop after 10 entries to avoid reading huge transcripts."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        transcript = tmp_path / "transcript.jsonl"
        # 10 short entries, then one long one → should NOT be found
        lines = [json.dumps({"type": "system", "content": "tiny"}) for _ in range(10)]
        lines.append(json.dumps({"type": "assistant", "content": "D" * 300}))
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        assert result == ""

    def test_concatenates_multiple_text_blocks(self, tmp_path: Path):
        """Multiple text blocks in one entry should be concatenated."""
        from piia_engram.hooks.auto_absorb_compact import _extract_compact_summary

        transcript = tmp_path / "transcript.jsonl"
        entry = {
            "type": "assistant",
            "content": [
                {"type": "text", "text": "E" * 120},
                {"type": "text", "text": "F" * 120},
            ],
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        result = _extract_compact_summary(str(transcript))
        # 120+120=240 ≥ 200 → should qualify
        assert "E" * 120 in result
        assert "F" * 120 in result


# ---------------------------------------------------------------------------
# main() integration tests
# ---------------------------------------------------------------------------


class TestPostCompactMainFlow:
    """Test main() with mocked Engram backend."""

    def test_exits_silently_on_empty_stdin(self, monkeypatch):
        """Empty stdin → no crash, no Engram calls."""
        from piia_engram.hooks import auto_absorb_compact

        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"read": lambda self: ""})())
        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.setattr("sys.argv", ["prog"])

        # Should not raise
        auto_absorb_compact.main()

    def test_exits_on_recursion_guard(self, monkeypatch):
        """If CLAUDE_INVOKED_BY=engram_recursive, exit immediately."""
        from piia_engram.hooks import auto_absorb_compact

        monkeypatch.setenv("CLAUDE_INVOKED_BY", "engram_recursive")
        monkeypatch.setattr("sys.argv", ["prog"])

        mock_stdin = type("FakeStdin", (), {
            "read": lambda self: json.dumps({"cwd": "/test", "transcript_path": "/t"})
        })()
        monkeypatch.setattr("sys.stdin", mock_stdin)

        # Should return without doing anything
        auto_absorb_compact.main()

    def test_exits_when_no_transcript_path(self, monkeypatch):
        """No transcript_path in stdin → early exit."""
        from piia_engram.hooks import auto_absorb_compact

        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.setattr("sys.argv", ["prog"])
        mock_stdin = type("FakeStdin", (), {
            "read": lambda self: json.dumps({"cwd": "/test"})
        })()
        monkeypatch.setattr("sys.stdin", mock_stdin)

        auto_absorb_compact.main()

    def test_calls_append_daily_log_only(self, tmp_path, monkeypatch):
        """Capture does no store work; drain archives once without extracting knowledge."""
        import io
        from piia_engram.hooks import auto_absorb_compact
        from piia_engram.hooks.spool import drain
        from piia_engram.core import Engram
        summary = "This is a comprehensive summary " * 10
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps({"content": summary}) + "\n", encoding="utf-8")
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.setattr("sys.argv", ["hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path), "transcript_path": str(transcript)})))
        with patch("piia_engram.core.Engram") as backend:
            assert auto_absorb_compact.main() == 0
            backend.assert_not_called()
        with patch.object(Engram, "extract_session_insights") as extraction:
            assert drain()["processed"] == 1
            extraction.assert_not_called()
        files = list((tmp_path / "store" / "daily").rglob("*.md"))
        assert len(files) == 1
        text = files[0].read_text(encoding="utf-8")
        assert "[compact]" in text and "claude_code" in text and summary in text

    def test_truncates_very_long_summaries(self, tmp_path, monkeypatch):
        import io
        from piia_engram.hooks import auto_absorb_compact
        from piia_engram.hooks.spool import drain
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps({"content": "X" * 5000}) + "\n", encoding="utf-8")
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.setattr("sys.argv", ["hook"])
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(transcript)})))
        assert auto_absorb_compact.main() == 0
        assert drain()["processed"] == 1
        text = next((tmp_path / "store" / "daily").rglob("*.md")).read_text(encoding="utf-8")
        assert "已截断" in text
        assert len(text) < 4000
    def test_argv_env_promotion(self):
        """--env KEY=VAL pairs should be promoted to os.environ."""
        from piia_engram.hooks.auto_absorb_compact import _apply_argv_env

        old = os.environ.get("_TEST_ABSORB_KEY")
        try:
            _apply_argv_env(["--env", "_TEST_ABSORB_KEY=hello_world"])
            assert os.environ.get("_TEST_ABSORB_KEY") == "hello_world"
        finally:
            if old is None:
                os.environ.pop("_TEST_ABSORB_KEY", None)
            else:
                os.environ["_TEST_ABSORB_KEY"] = old

    def test_engram_exception_swallowed(self, tmp_path, monkeypatch):
        """If Engram raises, the hook must not crash Claude Code."""
        from piia_engram.hooks import auto_absorb_compact

        transcript = tmp_path / "transcript.jsonl"
        entry = {"type": "assistant", "content": "Y" * 300}
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")

        stdin_data = json.dumps({
            "cwd": str(tmp_path),
            "transcript_path": str(transcript),
        })
        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.setattr("sys.argv", ["prog"])
        monkeypatch.setattr("sys.stdin", type("F", (), {"read": lambda self: stdin_data})())

        with patch("piia_engram.core.Engram", side_effect=RuntimeError("boom")):
            # Should not raise
            auto_absorb_compact.main()


# ---------------------------------------------------------------------------
# Doctor hook_specs integration
# ---------------------------------------------------------------------------


class TestDoctorPostCompactSpec:
    """Doctor should check for PostCompact hook presence."""

    def test_hook_modules_includes_auto_absorb_compact(self):
        """_HOOK_MODULES must have the PostCompact module registered."""
        from piia_engram.setup_wizard import _HOOK_MODULES
        assert "auto_absorb_compact" in _HOOK_MODULES
        assert _HOOK_MODULES["auto_absorb_compact"] == "piia_engram.hooks.auto_absorb_compact"
