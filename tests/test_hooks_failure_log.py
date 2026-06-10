"""Tests for hooks failure logging (_log.log_failure).

Hooks must never block the host tool, but failures must leave a
breadcrumb in ``<ENGRAM_DIR>/logs/hooks.log`` instead of vanishing
silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from piia_engram.hooks._log import log_failure


def _log_path(tmp_path: Path) -> Path:
    return tmp_path / "engram" / "logs" / "hooks.log"


class TestLogFailure:
    def test_writes_hook_name_and_exception(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))

        log_failure("my_hook", "save failed", ValueError("disk on fire"))

        text = _log_path(tmp_path).read_text(encoding="utf-8")
        assert "[my_hook]" in text
        assert "save failed" in text
        assert "ValueError" in text
        assert "disk on fire" in text

    def test_message_only_without_exception(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))

        log_failure("my_hook", "plain note")

        text = _log_path(tmp_path).read_text(encoding="utf-8")
        assert "[my_hook] plain note" in text

    def test_appends_across_calls(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))

        log_failure("hook_a", "first")
        log_failure("hook_b", "second")

        lines = _log_path(tmp_path).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert "[hook_a] first" in lines[0]
        assert "[hook_b] second" in lines[1]

    def test_never_raises_when_log_dir_unwritable(self, tmp_path, monkeypatch):
        """ENGRAM_DIR pointing at a *file* makes mkdir fail — must not raise."""
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setenv("ENGRAM_DIR", str(blocker))

        log_failure("my_hook", "save failed", RuntimeError("boom"))  # no raise

    def test_oversized_log_resets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
        path = _log_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("old\n" * 300_000, encoding="utf-8")  # > 1 MB

        log_failure("my_hook", "fresh entry")

        text = path.read_text(encoding="utf-8")
        assert "fresh entry" in text
        assert "old" not in text


class TestHookIntegration:
    """Failing Engram backends leave a breadcrumb instead of pure silence."""

    def test_auto_absorb_compact_logs_engram_failure(self, tmp_path, monkeypatch):
        from piia_engram.hooks import auto_absorb_compact

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.setattr("sys.argv", ["prog"])

        transcript = tmp_path / "transcript.jsonl"
        entry = {"type": "assistant", "content": "Y" * 300}
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        stdin_data = json.dumps(
            {"cwd": str(tmp_path), "transcript_path": str(transcript)}
        )
        monkeypatch.setattr(
            "sys.stdin", type("F", (), {"read": lambda self: stdin_data})()
        )

        with patch("piia_engram.core.Engram", side_effect=RuntimeError("boom")):
            auto_absorb_compact.main()  # must not raise

        text = _log_path(tmp_path).read_text(encoding="utf-8")
        assert "[auto_absorb_compact]" in text
        assert "boom" in text

    def test_auto_save_on_stop_logs_engram_failure(self, tmp_path, monkeypatch):
        from piia_engram.hooks import auto_save_on_stop

        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
        monkeypatch.setenv("CLAUDE_INVOKED_BY", "")
        monkeypatch.delenv("ENGRAM_MIN_TURNS_TO_FLUSH", raising=False)
        monkeypatch.setattr("sys.argv", ["prog"])

        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"type": "user", "timestamp": "2026-06-10T00:00:00Z"})
            for _ in range(8)
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")
        stdin_data = json.dumps(
            {"cwd": str(tmp_path), "transcript_path": str(transcript)}
        )
        monkeypatch.setattr(
            "sys.stdin", type("F", (), {"read": lambda self: stdin_data})()
        )

        with patch("piia_engram.core.Engram", side_effect=RuntimeError("boom")):
            auto_save_on_stop.main()  # must not raise

        text = _log_path(tmp_path).read_text(encoding="utf-8")
        assert "[auto_save_on_stop]" in text
        assert "boom" in text
