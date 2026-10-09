"""Tests for hooks failure logging (_log.log_failure).

Hooks must never block the host tool, but failures must leave a
breadcrumb in ``<ENGRAM_DIR>/logs/hooks.log`` instead of vanishing
silently.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from piia_engram.hooks._log import log_failure


def _log_path(tmp_path: Path) -> Path:
    return tmp_path / "engram" / "logs" / "hooks.log"


class TestLogFailure:
    @pytest.mark.parametrize("fallback", [False, True])
    def test_flushes_line_before_closing(self, tmp_path, monkeypatch, fallback):
        monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "engram"))
        events = []

        class Handle:
            def __enter__(self):
                return self
            def write(self, line):
                events.append(("write", line))
            def flush(self):
                events.append(("flush", None))
            def __exit__(self, *args):
                events.append(("close", None))

        if fallback:
            (tmp_path / "engram").write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr("builtins.open", lambda *args, **kwargs: Handle())
        monkeypatch.setattr(Path, "open", lambda *args, **kwargs: Handle())
        log_failure("test_hook", "budget exceeded")
        assert [event[0] for event in events] == ["write", "flush", "close"]
        assert "[test_hook] budget exceeded\n" in events[0][1]

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

    @pytest.mark.parametrize("module", ["auto_save_on_stop", "auto_absorb_compact",
                                       "cursor_save_on_stop", "cursor_writeback"])
    def test_capture_failure_logs_on_calling_thread(self, tmp_path, monkeypatch, module):
        from importlib import import_module
        from piia_engram.hooks import _producer

        calls = []
        def record(*args):
            calls.append((threading.current_thread(), args))

        monkeypatch.setattr(_producer, "log_failure", record)
        monkeypatch.setattr("sys.stdin", type("F", (), {"read": lambda self, size=-1: "{"})())
        monkeypatch.setattr("sys.argv", ["hook"])
        monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
        assert import_module("piia_engram.hooks." + module).main() == 0
        assert len(calls) == 1
        assert calls[0][0] is threading.current_thread()
        assert "capture failed (JSONDecodeError)" in calls[0][1][1]

    def test_read_failure_logs_on_calling_thread(self, monkeypatch):
        from piia_engram.hooks import _budget

        calls = []
        monkeypatch.setattr(_budget, "log_failure", lambda *args:
                            calls.append((threading.current_thread(), args)))
        def failed_read():
            raise OSError("read unavailable")

        assert _budget.read_with_budget(failed_read, "test_hook") == ""
        assert len(calls) == 1
        assert calls[0][0] is threading.current_thread()
        assert calls[0][1] == ("test_hook", "resume read failed (OSError)")

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
            "sys.stdin", type("F", (), {"read": lambda self, size=-1: stdin_data[:size] if size >= 0 else stdin_data})()
        )

        with patch("piia_engram.hooks.spool._publish", side_effect=RuntimeError("boom")):
            auto_absorb_compact.main()  # must not raise

        text = _log_path(tmp_path).read_text(encoding="utf-8")
        assert "[hook_spool]" in text
        assert "RuntimeError" in text

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
            "sys.stdin", type("F", (), {"read": lambda self, size=-1: stdin_data[:size] if size >= 0 else stdin_data})()
        )

        with patch("piia_engram.hooks.spool._publish", side_effect=RuntimeError("boom")):
            auto_save_on_stop.main()  # must not raise

        text = _log_path(tmp_path).read_text(encoding="utf-8")
        assert "[hook_spool]" in text
        assert "RuntimeError" in text
