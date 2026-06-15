"""v3.30 mechanism (5) — daily log guardrail tests.

``~/.engram/daily/<project-hash>/<YYYY-MM-DD>.md`` is the human-readable
per-project session timeline that wrap_up_session appends to. These tests
pin the file layout and append semantics so future refactors don't lose
the audit trail.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from piia_engram.core import Engram
from piia_engram.storage import _project_id


def _make(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def test_append_creates_dated_file_with_header(tmp_path: Path):
    e = _make(tmp_path)
    project = "Z:/Example Workspace/engram"
    r = e.append_daily_log(project, "First entry of the day.", event_type="session")
    assert r["created"] is True
    file = Path(r["file"])
    assert file.is_file()
    text = file.read_text(encoding="utf-8")
    today = datetime.now().strftime("%Y-%m-%d")
    assert f"# Daily Log · {today}" in text
    assert project in text
    assert "[session]" in text
    assert "First entry of the day." in text


def test_append_uses_stable_project_hash(tmp_path: Path):
    """Two paths with same case-insensitive normalized form must share log."""
    e = _make(tmp_path)
    r1 = e.append_daily_log("E:/Foo/Bar", "first", event_type="x")
    r2 = e.append_daily_log("E:\\Foo\\Bar", "second", event_type="y")
    # _project_id normalizes \ to /, so both should map to the same dir.
    assert Path(r1["file"]).parent == Path(r2["file"]).parent
    text = Path(r1["file"]).read_text(encoding="utf-8")
    assert "first" in text and "second" in text


def test_second_append_does_not_recreate(tmp_path: Path):
    e = _make(tmp_path)
    e.append_daily_log("/x", "one")
    r2 = e.append_daily_log("/x", "two")
    assert r2["created"] is False
    text = Path(r2["file"]).read_text(encoding="utf-8")
    # Single header, two entries
    assert text.count("# Daily Log") == 1
    assert "one" in text and "two" in text


def test_no_project_folder_falls_back_to_bucket(tmp_path: Path):
    """Empty project_folder must still log under a synthetic bucket."""
    e = _make(tmp_path)
    r = e.append_daily_log("", "orphan entry")
    assert "(no-project)" in Path(r["file"]).read_text(encoding="utf-8")


def test_event_type_appears_in_header(tmp_path: Path):
    e = _make(tmp_path)
    for tag in ("session", "lesson", "decision", "checkpoint", "manual"):
        e.append_daily_log("/p", f"content-{tag}", event_type=tag)
    text = (tmp_path / "daily" / _project_id("/p")
            / f"{datetime.now().strftime('%Y-%m-%d')}.md").read_text(
        encoding="utf-8",
    )
    for tag in ("session", "lesson", "decision", "checkpoint", "manual"):
        assert f"[{tag}]" in text
        assert f"content-{tag}" in text


def test_source_tool_shown_in_header_when_provided(tmp_path: Path):
    e = _make(tmp_path)
    e.append_daily_log("/p", "with src", event_type="session", source_tool="claude_code")
    text = (tmp_path / "daily" / _project_id("/p")
            / f"{datetime.now().strftime('%Y-%m-%d')}.md").read_text(
        encoding="utf-8",
    )
    assert "claude_code" in text


def test_get_returns_empty_when_no_log_exists(tmp_path: Path):
    e = _make(tmp_path)
    r = e.get_daily_log("/never-touched")
    assert r["exists"] is False
    assert r["content"] == ""
    # file path is still returned so callers can know where to write
    assert r["file"].endswith(".md")


def test_get_returns_full_content_after_append(tmp_path: Path):
    e = _make(tmp_path)
    e.append_daily_log("/p", "alpha", event_type="session")
    e.append_daily_log("/p", "beta", event_type="lesson")
    r = e.get_daily_log("/p")
    assert r["exists"] is True
    assert "alpha" in r["content"] and "beta" in r["content"]
    assert "[session]" in r["content"] and "[lesson]" in r["content"]


def test_get_with_explicit_date_for_yesterday(tmp_path: Path):
    """Passing a past date must return non-existent (no rolling default)."""
    e = _make(tmp_path)
    e.append_daily_log("/p", "today", event_type="session")
    r = e.get_daily_log("/p", date="2020-01-01")
    assert r["exists"] is False
    assert r["date"] == "2020-01-01"
    assert r["content"] == ""
