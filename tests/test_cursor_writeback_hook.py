import io
import json


def _run_hook(monkeypatch, payload: dict):
    from piia_engram.hooks import cursor_writeback

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return cursor_writeback.main()


def test_cursor_writeback_disabled_by_default_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK", raising=False)
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)

    assert _run_hook(monkeypatch, {
        "summary": (
            "Remember to derive validation isolation metadata from path containment "
            "under the test root because it prevents false evidence claims."
        )
    }) == 0

    assert not (tmp_path / "knowledge" / "lessons.json").exists()


def test_cursor_writeback_enabled_saves_only_staging(tmp_path, monkeypatch):
    from piia_engram.core import Engram

    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)

    assert _run_hook(monkeypatch, {
        "summary": (
            "Remember to derive validation isolation metadata from path containment "
            "under the test root because it prevents false evidence claims."
        )
    }) == 0

    lessons = Engram().get_lessons(limit=None, _update_access=False)
    assert len(lessons) == 1
    assert lessons[0]["tier"] == "staging"
    assert lessons[0]["source_tool"] == "cursor"


def test_cursor_writeback_reentry_guard_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", "1")

    assert _run_hook(monkeypatch, {
        "summary": "Remember to keep this hook from recursively writing staging items."
    }) == 0

    assert not (tmp_path / "knowledge" / "lessons.json").exists()


def test_cursor_writeback_reads_transcript_path(tmp_path, monkeypatch):
    from piia_engram.core import Engram

    transcript = tmp_path / "cursor.jsonl"
    transcript.write_text(
        json.dumps({
            "content": (
                "Remember to derive validation isolation metadata from path "
                "containment under the test root because it prevents false "
                "evidence claims."
            )
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_TEST", "1")
    monkeypatch.setenv("ENGRAM_CURSOR_WRITEBACK", "1")
    monkeypatch.delenv("ENGRAM_CURSOR_WRITEBACK_ACTIVE", raising=False)

    assert _run_hook(monkeypatch, {"transcript_path": str(transcript)}) == 0

    lessons = Engram().get_lessons(limit=None, _update_access=False)
    assert len(lessons) == 1
    assert lessons[0]["tier"] == "staging"
