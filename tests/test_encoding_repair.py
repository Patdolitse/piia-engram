import json
from pathlib import Path

from piia_engram.core import Engram


def _gbk_mojibake(text: str) -> str:
    return text.encode("utf-8").decode("gbk")


def test_repair_text_fixes_utf8_decoded_as_gbk():
    from piia_engram.encoding_repair import repair_text

    original = "发布流程测试"
    damaged = _gbk_mojibake(original)

    repaired = repair_text(damaged)

    assert repaired.changed is True
    assert repaired.text == original
    assert repaired.reason == "utf8_as_gbk"


def test_repair_text_leaves_valid_chinese_unchanged():
    from piia_engram.encoding_repair import repair_text

    original = "开发流程检查：不要把正常中文误修。"

    repaired = repair_text(original)

    assert repaired.changed is False
    assert repaired.text == original


def test_normalize_entry_text_repairs_nested_playbook_fields():
    from piia_engram.encoding_repair import normalize_entry_text

    entry = {
        "title": _gbk_mojibake("发布流程测试"),
        "steps": [
            {"action": _gbk_mojibake("流程测试"), "detail": "正常中文保留"},
        ],
        "id": "abc123",
        "source_url": "https://example.com/%E4%B8%AD%E6%96%87",
    }

    normalized, changes = normalize_entry_text(entry)

    assert normalized["title"] == "发布流程测试"
    assert normalized["steps"][0]["action"] == "流程测试"
    assert normalized["steps"][0]["detail"] == "正常中文保留"
    assert normalized["source_url"] == entry["source_url"]
    assert {c.path for c in changes} == {"title", "steps[0].action"}


def test_scan_and_repair_engram_root_backs_up_and_fixes_json(tmp_path: Path):
    from piia_engram.encoding_repair import repair_engram_root, scan_engram_root

    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    lessons_path = knowledge / "lessons.json"
    lessons_path.write_text(
        json.dumps(
            [
                {
                    "id": "lesson-1",
                    "summary": _gbk_mojibake("发布流程测试"),
                    "detail": "正常中文保留",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    scan = scan_engram_root(tmp_path)

    assert scan.repairable_count == 1
    assert scan.findings[0].relative_path == Path("knowledge") / "lessons.json"
    assert scan.findings[0].json_path == "[0].summary"

    report = repair_engram_root(tmp_path, apply=True)

    assert report.changed_files == [lessons_path]
    assert report.backup_dir is not None
    assert (report.backup_dir / "knowledge" / "lessons.json").is_file()
    fixed = json.loads(lessons_path.read_text(encoding="utf-8"))
    assert fixed[0]["summary"] == "发布流程测试"
    assert fixed[0]["detail"] == "正常中文保留"


def test_scan_and_repair_engram_root_fixes_markdown_text(tmp_path: Path):
    from piia_engram.encoding_repair import repair_engram_root, scan_engram_root

    ctx_dir = tmp_path / "contexts" / "codex"
    ctx_dir.mkdir(parents=True)
    context_path = ctx_dir / "session.md"
    context_path.write_text(
        "# Session\n\n" + _gbk_mojibake("发布流程测试") + "\n",
        encoding="utf-8",
    )

    scan = scan_engram_root(tmp_path)

    assert scan.repairable_count == 1
    assert scan.findings[0].relative_path == Path("contexts") / "codex" / "session.md"
    assert scan.findings[0].json_path == "line 3"

    report = repair_engram_root(tmp_path, apply=True)

    assert report.changed_files == [context_path]
    assert "发布流程测试" in context_path.read_text(encoding="utf-8")


def test_scan_reports_unrepairable_mojibake_without_writing(tmp_path: Path):
    from piia_engram.encoding_repair import repair_engram_root, scan_engram_root

    kdir = tmp_path / "knowledge"
    kdir.mkdir(parents=True)
    lessons_path = kdir / "lessons.json"
    damaged = "\u5bee\u20ac\u9359?"  # irreversible mojibake with replacement '?'
    lessons_path.write_text(
        json.dumps([{"id": "l1", "summary": damaged}], ensure_ascii=False),
        encoding="utf-8",
    )

    scan = scan_engram_root(tmp_path)

    assert scan.repairable_count == 0
    assert scan.suspect_count == 1
    assert scan.findings[0].repairable is False

    report = repair_engram_root(tmp_path, apply=True)

    assert report.changed_files == []
    assert json.loads(lessons_path.read_text(encoding="utf-8"))[0]["summary"] == damaged


def test_scan_does_not_flag_valid_rare_chinese_words(tmp_path: Path):
    from piia_engram.encoding_repair import scan_engram_root

    kdir = tmp_path / "knowledge"
    kdir.mkdir(parents=True)
    (kdir / "lessons.json").write_text(
        json.dumps(
            [
                {
                    "id": "l1",
                    "summary": "评论要短且有锋芒，避免模板化格式。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    scan = scan_engram_root(tmp_path)

    assert scan.findings == []


def test_add_lesson_repairs_high_confidence_mojibake_before_persisting(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENGRAM_TEST", "1")
    engram = Engram(tmp_path)

    result = engram.add_lesson(
        {
            "summary": _gbk_mojibake("发布流程测试"),
            "detail": "正常中文保留",
            "domain": "encoding",
        }
    )

    assert result["summary"] == "发布流程测试"
    stored = json.loads((tmp_path / "knowledge" / "lessons.json").read_text(encoding="utf-8"))
    assert stored[0]["summary"] == "发布流程测试"
