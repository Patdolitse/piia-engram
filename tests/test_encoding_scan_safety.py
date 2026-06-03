"""Safety guards for the encoding/history data scan (Node N8).

Two invariants that make `engram repair-encoding` a safe closure path for the
historical mojibake concern:

1. The dry-run scan never mutates stored files.
2. The metadata-only summary leaks no body text and no paths, so it is safe to
   include in an audit/report (unlike the per-finding owner view).
"""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram.encoding_repair import scan_engram_root, summarize_findings


def _gbk_mojibake(text: str) -> str:
    return text.encode("utf-8").decode("gbk")


def _seed_root(tmp_path: Path) -> dict[str, bytes]:
    """Create an Engram-like root with mojibake; return original raw bytes."""
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    lessons = knowledge / "lessons.json"
    lessons.write_text(
        json.dumps(
            [{"id": "L1", "summary": _gbk_mojibake("发布流程测试"), "detail": "正常中文"}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx = tmp_path / "contexts" / "codex"
    ctx.mkdir(parents=True)
    session = ctx / "session.md"
    session.write_text("# Session\n\n" + _gbk_mojibake("发布流程测试") + "\n", encoding="utf-8")
    return {
        str(lessons): lessons.read_bytes(),
        str(session): session.read_bytes(),
    }


def test_dry_run_scan_does_not_mutate_files(tmp_path: Path):
    before = _seed_root(tmp_path)

    report = scan_engram_root(tmp_path)
    assert report.repairable_count >= 1  # the scan actually found the mojibake

    # Every seeded file is byte-identical after the scan (no write happened).
    for path_str, original_bytes in before.items():
        assert Path(path_str).read_bytes() == original_bytes


def test_summary_is_metadata_only_no_body_or_path(tmp_path: Path):
    _seed_root(tmp_path)
    report = scan_engram_root(tmp_path)
    summary = summarize_findings(report)

    # Shape: counts + generic reason codes only.
    assert set(summary) == {
        "files_with_findings",
        "total_findings",
        "repairable_count",
        "suspect_count",
        "reasons",
    }
    assert summary["files_with_findings"] >= 1
    assert summary["repairable_count"] >= 1

    serialized = json.dumps(summary, ensure_ascii=False)
    # No repaired/original body text leaks.
    assert "发布流程测试" not in serialized
    # No filesystem paths leak (neither file names nor json paths).
    assert "lessons.json" not in serialized
    assert "session.md" not in serialized
    assert "knowledge" not in serialized
    assert "contexts" not in serialized
    # Reason codes are generic, non-sensitive labels.
    for reason in summary["reasons"]:
        assert "/" not in reason and "\\" not in reason
