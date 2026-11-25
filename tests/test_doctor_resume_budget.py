"""Issue #91: ``engram doctor`` reports recent resume-hook budget timeouts.

A timed-out SessionStart read looks like success to the host (exit 0, no
context), so doctor surfaces the hook's own ``hooks.log`` entries: a warning
with the count from the last seven days and a hint, never a log body or path.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from piia_engram import doctor
from test_doctor_connections import (  # noqa: F401  (fixtures and harness)
    _SECRET, _doctor_text, _snapshot, home, successful_startup_probe, world,
)

MESSAGE = "resume read budget exceeded"
NOW = datetime(2026, 11, 25, 12, 0, 0)
WARNING = "Resume brief hook: 2 budget timeout(s) in the last 7 days"


def _stamp(days: float = 0) -> str:
    moment = datetime.now() - timedelta(days=days)
    return moment.replace(microsecond=0).isoformat(timespec="seconds")


def _lines(*entries) -> str:
    return "".join(f"{stamp} [{hook}] {message}\n" for stamp, hook, message in entries)


def _write_log(store: Path, text: str) -> Path:
    path = store / "logs" / "hooks.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# One timeout per host: Claude Code and the Cursor equivalent.
RECENT = [
    (_stamp(6.5), "auto_inject_resume_brief", MESSAGE),
    (_stamp(0.1), "cursor_inject_resume_brief", MESSAGE),
]
IGNORED = [
    (_stamp(8), "auto_inject_resume_brief", MESSAGE),  # older than the window
    (_stamp(0.3), "auto_inject_resume_brief", "resume read failed (OSError)"),
    (_stamp(0.4), "auto_save_on_stop", f"capture failed (RuntimeError) {_SECRET}"),
]


def test_doctor_warns_with_the_count_and_names_the_override(world, successful_startup_probe):
    _write_log(world["store"], _lines(*RECENT, *IGNORED))
    before = _snapshot(world["store"], world["home"])

    out = _doctor_text()

    assert WARNING in out
    line = next(part for part in out.splitlines() if WARNING in part)
    assert line.startswith("    [--] ") and "ENGRAM_RESUME_HOOK_BUDGET_S" in line
    assert _snapshot(world["store"], world["home"]) == before  # read-only: the log is untouched
    assert _SECRET not in out and "capture failed" not in out  # no log body
    assert "hooks.log" not in out  # no new path beyond doctor's usual display


def test_doctor_is_quiet_without_recent_timeouts(world, successful_startup_probe):
    assert "Resume brief hook" not in _doctor_text()  # no hooks.log at all

    _write_log(world["store"], _lines(*IGNORED))

    out = _doctor_text()

    assert "Resume brief hook" not in out and "ENGRAM_RESUME_HOOK_BUDGET_S" not in out


def test_encrypted_store_path_still_reports_the_timeouts(world, monkeypatch, capsys):
    _write_log(world["store"], _lines(*RECENT))
    monkeypatch.setattr(doctor, "_encrypted_diagnostics", lambda root: True)

    assert doctor._run_functional_checks(fix=False) == 0

    out = capsys.readouterr().out
    assert "Encrypted-store functional checks skipped" in out and WARNING in out
    assert _SECRET not in out


def test_check_counts_only_recent_budget_entries(tmp_path):
    store = tmp_path / "store"
    recent = (NOW - timedelta(days=1)).isoformat(timespec="seconds")
    edge = (NOW - timedelta(days=7)).isoformat(timespec="seconds")
    old = (NOW - timedelta(days=7, seconds=1)).isoformat(timespec="seconds")
    clock_skew = (NOW + timedelta(minutes=5)).isoformat(timespec="seconds")
    _write_log(store, _lines(
        (recent, "auto_inject_resume_brief", MESSAGE),
        (recent, "cursor_inject_resume_brief", MESSAGE),
        (clock_skew, "auto_inject_resume_brief", MESSAGE),
        (old, "auto_inject_resume_brief", MESSAGE),
        (edge, "auto_inject_resume_brief", MESSAGE),  # exactly seven days old: inside
        ("not a timestamp", "auto_inject_resume_brief", MESSAGE),  # undated: ignored
        (recent, "auto_inject_resume_brief", "resume read failed (OSError)"),
        (recent, "auto_save_on_stop", "capture failed (RuntimeError)"),
    ))

    finding = doctor.resume_budget_check(store, now=NOW)

    assert finding == {
        "name": "resume_hook_budget", "status": "WARN", "count": 4, "window_days": 7,
        "read_only": True, "unreadable": "", "hint": finding["hint"]}
    assert "ENGRAM_RESUME_HOOK_BUDGET_S" in finding["hint"]
    assert str(store) not in json.dumps(finding)


def test_check_creates_nothing_and_ignores_undecodable_bytes(tmp_path):
    absent = tmp_path / "absent"

    finding = doctor.resume_budget_check(absent, now=NOW)

    assert finding["status"] == "PASS" and finding["count"] == 0
    assert not absent.exists()  # nothing was created
    path = tmp_path / "logs" / "hooks.log"
    path.parent.mkdir()
    path.write_bytes(b"\xff\xfe")  # undecodable bytes are not a timeout entry
    assert doctor.resume_budget_check(tmp_path, now=NOW)["count"] == 0


def test_unreadable_log_is_reported_by_error_type_only(tmp_path, monkeypatch, capsys):
    private = str(tmp_path / "private-dir-name" / "logs" / "hooks.log")
    real_read_text = Path.read_text

    def denied(self, *args, **kwargs):
        if str(self).endswith("hooks.log"):
            raise PermissionError(13, "denied", private)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)

    finding = doctor.resume_budget_check(tmp_path)

    assert finding["status"] == "WARN" and finding["count"] == 0
    assert finding["unreadable"] == "PermissionError" and finding["hint"] == ""
    doctor._print_resume_budget_check(tmp_path)
    out = capsys.readouterr().out
    assert "Resume hook budget check skipped (PermissionError)" in out
    assert "private-dir-name" not in out and "Resume brief hook" not in out
