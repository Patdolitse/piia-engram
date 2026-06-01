"""Regression corpus for automatic knowledge extraction quality."""

from __future__ import annotations

from pathlib import Path

from piia_engram import Engram
from piia_engram.context import ingest_extraction


def _engram(root: Path) -> Engram:
    return Engram(root)


def _knowledge_counts(engram: Engram) -> tuple[int, int]:
    lessons = engram.get_lessons(limit=None, _update_access=False)
    decisions = engram.get_decisions(limit=None, _update_access=False)
    return len(lessons), len(decisions)


def _assert_auto_metadata(engram: Engram) -> None:
    for item in engram.get_lessons(limit=None, _update_access=False):
        assert item["tier"] == "staging"
        assert item["extraction"]["quality_score"] >= 0.55
        assert item["extraction"]["quality_signals"]
    for item in engram.get_decisions(limit=None, _update_access=False):
        assert item["tier"] == "staging"
        assert item["extraction"]["quality_score"] >= 0.55
        assert item["extraction"]["quality_signals"]


def _assert_rejected_quality_schema(rejected: dict) -> None:
    assert set(rejected) == {
        "count",
        "flags",
        "candidate_types",
        "score_min",
        "score_max",
    }


def test_session_insights_quality_corpus(tmp_path: Path):
    """Session summaries should keep durable knowledge and reject planning chatter."""
    cases = [
        {
            "name": "english_lesson",
            "summary": (
                "Remember to run twine check before publishing because it catches "
                "package metadata errors."
            ),
            "lessons": 1,
            "decisions": 0,
        },
        {
            "name": "english_decision",
            "summary": "We decided to use PostgreSQL for the audit ledger because it is durable.",
            "lessons": 0,
            "decisions": 1,
        },
        {
            "name": "chinese_release_rule",
            "summary": "注意：PyPI 包名需要提前确认是否被占用。",
            "lessons": 1,
            "decisions": 0,
        },
        {
            "name": "chinese_decision",
            "summary": "最终决定使用 FastAPI 作为后端框架。",
            "lessons": 0,
            "decisions": 1,
        },
        {
            "name": "planning_noise",
            "summary": (
                "We discussed whether to add graph retrieval later. "
                "Maybe we should evaluate a memory graph next week."
            ),
            "lessons": 0,
            "decisions": 0,
        },
        {
            "name": "loose_status",
            "summary": "Today we talked about several possible ideas and left them open.",
            "lessons": 0,
            "decisions": 0,
        },
    ]

    failures: list[str] = []
    for case in cases:
        eng = _engram(tmp_path / case["name"])
        result = eng.extract_session_insights(case["summary"], source_tool="test")
        lessons, decisions = _knowledge_counts(eng)
        if lessons != case["lessons"] or decisions != case["decisions"]:
            failures.append(f"{case['name']}: got {lessons}/{decisions}, expected "
                            f"{case['lessons']}/{case['decisions']}; result={result}")
        if case["lessons"] or case["decisions"]:
            _assert_auto_metadata(eng)
        else:
            assert result["skipped"] >= 1

    assert failures == []


def test_ingest_notes_quality_corpus(tmp_path: Path):
    """Free-form notes should use the same quality gate as session extraction."""
    cases = [
        {
            "name": "technical_note_without_trigger",
            "notes": "pip install 出现依赖冲突时用 venv 隔离",
            "lessons": 1,
            "decisions": 0,
        },
        {
            "name": "english_discovery",
            "notes": "discovered that connection pooling reduces latency by 40%",
            "lessons": 1,
            "decisions": 0,
        },
        {
            "name": "english_decision",
            "notes": "decided to use uv for reproducible tool runs",
            "lessons": 0,
            "decisions": 1,
        },
        {
            "name": "future_plan",
            "notes": "Maybe consider a memory graph benchmark next week",
            "lessons": 0,
            "decisions": 0,
        },
        {
            "name": "loose_status",
            "notes": "This is a loose status update without a durable lesson",
            "lessons": 0,
            "decisions": 0,
        },
    ]

    failures: list[str] = []
    for case in cases:
        eng = _engram(tmp_path / case["name"])
        result = eng.ingest_notes(case["notes"], source_tool="test")
        lessons, decisions = _knowledge_counts(eng)
        if lessons != case["lessons"] or decisions != case["decisions"]:
            failures.append(f"{case['name']}: got {lessons}/{decisions}, expected "
                            f"{case['lessons']}/{case['decisions']}; result={result}")
        if case["lessons"] or case["decisions"]:
            _assert_auto_metadata(eng)
        else:
            assert result["skipped_low_quality"] >= 1

    assert failures == []


def test_ingest_extraction_quality_corpus(tmp_path: Path):
    """LLM structured extraction should reject weak plans but keep explicit choices."""
    eng = _engram(tmp_path)
    extracted = {
        "lessons": [
            {"summary": "Always run twine check before publishing", "confidence": 0.9},
            {"summary": "Maybe consider graph memory later", "confidence": 0.2},
        ],
        "decisions": [
            {
                "question": "Which release gate should we use?",
                "choice": "rollback rehearsal",
                "confidence": 0.8,
                "tier": "verified",
            },
            {
                "question": "Should we evaluate graph memory later?",
                "choice": "maybe",
                "confidence": 0.2,
            },
        ],
    }

    result = ingest_extraction(eng, extracted, str(tmp_path), session_id="quality-corpus")

    assert result["items_learned"] == 2
    assert result["skipped_low_quality"] == 2
    lessons, decisions = _knowledge_counts(eng)
    assert lessons == 1
    assert decisions == 1
    _assert_auto_metadata(eng)


def test_ingest_notes_rejected_quality_summary_is_metadata_only(tmp_path: Path):
    """Rejected note candidates should expose tuning metadata without raw text."""
    eng = _engram(tmp_path)
    notes = "Maybe consider a memory graph benchmark next week"

    result = eng.ingest_notes(notes, source_tool="test")

    rejected = result["rejected_quality"]
    _assert_rejected_quality_schema(rejected)
    assert rejected["count"] == 1
    assert rejected["flags"]["planning_or_uncertain"] == 1
    assert rejected["candidate_types"]["lesson"] == 1
    assert rejected["score_min"] is not None
    assert rejected["score_max"] is not None
    assert "Maybe consider" not in str(rejected)
    assert "memory graph benchmark" not in str(rejected)


def test_session_insights_rejected_quality_summary_is_metadata_only(tmp_path: Path):
    """Session extraction should summarize rejected quality flags without bodies."""
    eng = _engram(tmp_path)
    summary = (
        "We discussed whether to add graph retrieval later. "
        "Maybe we should evaluate a memory graph next week."
    )

    result = eng.extract_session_insights(summary, source_tool="test")

    rejected = result["rejected_quality"]
    _assert_rejected_quality_schema(rejected)
    assert rejected["count"] >= 1
    assert rejected["flags"]["planning_or_uncertain"] >= 1
    assert "graph retrieval" not in str(rejected)
    assert "memory graph" not in str(rejected)


def test_ingest_extraction_rejected_quality_summary_is_metadata_only(tmp_path: Path):
    """LLM extraction rejects should return aggregate flags, not rejected bodies."""
    eng = _engram(tmp_path)
    extracted = {
        "lessons": [
            {"summary": "Maybe consider graph memory later", "confidence": 0.2},
        ],
        "decisions": [
            {
                "question": "Should we evaluate graph memory later?",
                "choice": "maybe",
                "confidence": 0.2,
            },
        ],
    }

    result = ingest_extraction(eng, extracted, str(tmp_path), session_id="quality-corpus")

    rejected = result["rejected_quality"]
    _assert_rejected_quality_schema(rejected)
    assert rejected["count"] == 2
    assert rejected["flags"]["planning_or_uncertain"] == 2
    assert rejected["candidate_types"]["lesson"] == 1
    assert rejected["candidate_types"]["decision"] == 1
    assert "Maybe consider" not in str(rejected)
    assert "Should we evaluate" not in str(rejected)


def test_auto_extraction_rejects_ephemeral_personal_reminders(tmp_path: Path):
    """Short-lived reminders should not become durable lessons."""
    reminder = "Remember to send Alice the status update tomorrow"

    notes_eng = _engram(tmp_path / "notes")
    notes_result = notes_eng.ingest_notes(reminder, source_tool="test")
    assert _knowledge_counts(notes_eng) == (0, 0)
    assert notes_result["rejected_quality"]["flags"]["ephemeral_todo"] == 1
    assert "Alice" not in str(notes_result["rejected_quality"])

    session_eng = _engram(tmp_path / "session")
    session_result = session_eng.extract_session_insights(reminder, source_tool="test")
    assert _knowledge_counts(session_eng) == (0, 0)
    assert session_result["rejected_quality"]["flags"]["ephemeral_todo"] == 1

    llm_eng = _engram(tmp_path / "llm")
    extracted = {"lessons": [{"summary": reminder, "confidence": 0.95}]}
    llm_result = ingest_extraction(llm_eng, extracted, str(tmp_path), session_id="ephemeral")
    assert _knowledge_counts(llm_eng) == (0, 0)
    assert llm_result["rejected_quality"]["flags"]["ephemeral_todo"] == 1


def test_metric_backed_operational_findings_are_kept(tmp_path: Path):
    """Concrete measured outcomes are durable even without an explicit lesson verb."""
    eng = _engram(tmp_path)
    notes = (
        "Found that connection pooling reduced API latency by 38 percent "
        "after switching to Redis"
    )

    result = eng.ingest_notes(notes, source_tool="test")

    assert result["saved_lessons"] == 1
    lessons = eng.get_lessons(limit=None, _update_access=False)
    assert lessons[0]["tier"] == "staging"
    signals = lessons[0]["extraction"]["quality_signals"]
    assert "measured_outcome" in signals
    assert "evidence_or_outcome" in signals
