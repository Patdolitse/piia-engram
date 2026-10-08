"""Session knowledge extraction: whole candidates and honest skip reporting.

1. file names, versions, URLs and decimals stay inside one candidate;
2. Chinese and English full stops still split; long lines and separate lines are
   never merged;
3. every candidate maps back to a contiguous span of the source summary, and every
   skip says why, so "split correctly but not good enough" is told apart from
   "split wrongly";
4. skips, their reasons and new rows are reported next to (not hidden behind) the
   snapshot's "saved".
All on a throwaway store.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from piia_engram.core import Engram
from piia_engram.session_filters import split_sentences

SUMMARY = (
    "We decided to keep the loader in module.py because the plugin API changed. "
    "Always regenerate REPORT.md after editing data.json, otherwise the dashboard shows stale numbers. "
    "The crash came from libsample.so being built against v2.3.1 instead of v2.4.0, so we must pin v2.4.0. "
    "Remember to download the schema from https://example.com/schemas/v1.2/config.json before validating. "
    "我们决定改用 config.v2.yaml，因为旧格式在 3.14 版本里被移除了。"
    "注意 build.sh 必须在 tools/ 目录下运行，否则找不到 lib.so。"
)
SENTENCES = [
    "We decided to keep the loader in module.py because the plugin API changed",
    "Always regenerate REPORT.md after editing data.json, otherwise the dashboard shows stale numbers",
    "The crash came from libsample.so being built against v2.3.1 instead of v2.4.0, so we must pin v2.4.0",
    "Remember to download the schema from https://example.com/schemas/v1.2/config.json before validating",
    "我们决定改用 config.v2.yaml，因为旧格式在 3.14 版本里被移除了",
    "注意 build.sh 必须在 tools/ 目录下运行，否则找不到 lib.so",
]


def _pieces(text: str) -> list[str]:
    return [p.strip() for p in split_sentences(text) if p.strip()]


def _candidate_text(row: dict) -> str:
    return str(row.get("text") or row.get("summary") or row.get("title") or "")


# ---------------------------------------------------------------- 1 and 2: splitting


def test_the_acceptance_vector_splits_into_whole_sentences():
    assert _pieces(SUMMARY) == SENTENCES


def test_full_stops_split_and_lines_are_never_merged():
    long_line = "Keep the retry loop in fetch.py bounded " + "and log each attempt " * 30
    text = f"第一句写在这里。Second one here! Third?\n{long_line}\nline without stop\nanother line"
    pieces = _pieces(text)
    assert pieces[:3] == ["第一句写在这里", "Second one here", "Third"]
    assert pieces[3] == long_line.strip()
    assert pieces[4:] == ["line without stop", "another line"]


# ---------------------------------------------------------------- 3: correspondence


@pytest.fixture
def eng(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engram:
    monkeypatch.setenv("ENGRAM_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("ENGRAM_RECONCILE", "0")
    return Engram(root=tmp_path / "store")


def test_every_candidate_maps_back_to_a_source_span(eng):
    result = eng.extract_session_insights(SUMMARY, source_tool="test")

    rows = [r for r in result["results"] if _candidate_text(r)]
    assert rows
    for row in rows:
        text = _candidate_text(row)
        assert text in SUMMARY, row
        # a candidate starts at a sentence start: never in the middle of a file name
        assert any(sentence.startswith(text) for sentence in SENTENCES), row
    saved = eng.get_lessons(limit=None, _update_access=False) + eng.get_decisions(limit=None)
    for item in saved:
        claim = item.get("summary") or item.get("title") or item.get("question") or ""
        assert claim in SENTENCES, claim


def test_every_skip_says_why(eng):
    result = eng.extract_session_insights(SUMMARY, source_tool="test")

    skipped = [r for r in result["results"] if r.get("status") == "skipped"]
    assert skipped
    assert all(r.get("reason") for r in skipped), skipped


def test_skip_count_matches_the_listed_skips(eng):
    result = eng.extract_session_insights(SUMMARY + "\n\n", source_tool="test")

    listed = [r for r in result["results"] if r.get("status") == "skipped"]
    assert result["skipped"] == len(listed)
    assert sum(result["skipped_by_reason"].values()) == result["skipped"]


# ---------------------------------------------------------------- 4: reporting


def _wrap_up(tmp_path: Path, eng: Engram, summary: str) -> dict:
    import piia_engram.mcp_server as mcp_server

    mcp_server._engram = eng
    out = asyncio.run(mcp_server.wrap_up_session(
        summary, project_folder=str(tmp_path / "proj"), source_tool="test", user_confirmed=True,
    ))
    return json.loads(out)


def test_wrap_up_reports_skips_next_to_the_snapshot_save(tmp_path, eng):
    nothing_new = (
        "The build ran on Tuesday. The logs are in the usual place. "
        "Remember the release.yml job. Deploy happened after lunch."
    )

    report = _wrap_up(tmp_path, eng, nothing_new)

    stage = report["maintenance"]["extract_session_insights"]
    assert report["project_snapshot"]["saved"] is True
    assert stage["saved_lessons"] == 0 and stage["saved_decisions"] == 0
    assert stage["skipped"] == report["insights"]["skipped"] > 0
    assert stage["skipped_by_reason"] == report["insights"]["skipped_by_reason"]
    stage_counts = report["operation"]["stages"]["extract_session_insights"]["counts"]
    assert stage_counts["skipped"] == stage["skipped"]
