"""Regression corpus for automatic knowledge extraction quality."""

from __future__ import annotations

import json
from pathlib import Path

from piia_engram import Engram
from piia_engram.context import ingest_extraction


def _engram(root: Path) -> Engram:
    return Engram(root)


def _knowledge_counts(engram: Engram, project_folder: str | None = None) -> tuple[int, int]:
    lessons = engram.get_lessons(
        project_folder=project_folder,
        limit=None,
        _update_access=False,
    )
    decisions = engram.get_decisions(
        project_folder=project_folder,
        limit=None,
        _update_access=False,
    )
    return len(lessons), len(decisions)


def _assert_auto_metadata(engram: Engram, project_folder: str | None = None) -> None:
    items = (
        engram.get_lessons(
            project_folder=project_folder,
            limit=None,
            _update_access=False,
        )
        + engram.get_decisions(
            project_folder=project_folder,
            limit=None,
            _update_access=False,
        )
    )
    for item in items:
        # Risk-based write gate: high-risk content is review-gated to staging,
        # everything else auto-absorbs to verified. Tier must track risk level.
        if item.get("risk_level") == "high":
            assert item["tier"] == "staging"
            assert item["approval_status"] == "pending"
        else:
            assert item["tier"] == "verified"
            assert item["approval_status"] == "approved"
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
            "summary": "注意：PyPI 包名需要提前确认是否被占用，因为重复包名会导致发布失败。",
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
            "name": "chinese_completed_lesson_with_evidence",
            "summary": (
                "我已经验证 exact project_id 隔离有效，因为相邻项目正文未出现在测试结果中。"
            ),
            "lessons": 1,
            "decisions": 0,
        },
        {
            "name": "chinese_completed_decision",
            "summary": "我已经决定保留人工 review_needed，不做自动晋升。",
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
    lessons, decisions = _knowledge_counts(eng, project_folder=str(tmp_path))
    assert lessons == 1
    assert decisions == 1
    _assert_auto_metadata(eng, project_folder=str(tmp_path))


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
    # Low-risk operational finding auto-absorbs to verified under the risk gate.
    assert lessons[0]["tier"] == "verified"
    signals = lessons[0]["extraction"]["quality_signals"]
    assert "measured_outcome" in signals
    assert "evidence_or_outcome" in signals


def test_session_extraction_suppresses_delegation_and_process_noise(tmp_path: Path):
    """Copied task prompts and assistant status updates are not memory candidates."""
    eng = _engram(tmp_path)
    summary = """
<codex_delegation>
  <source_thread_id>synthetic-source</source_thread_id>
  <input>必须把复制的任务规则保存成长期经验。最终决定采用错误方案。</input>
</codex_delegation>
我正在核验实现，接下来会修改作用域逻辑。
Tests: pytest tests/test_project_scope_resume_cycle.py passed.
We decided to keep project resume exact-scope by default.
Lesson: exact project resume should require a project_id match because global fallback leaked in synthetic tests.
"""

    result = eng.extract_session_insights(
        summary,
        source_tool="codex",
        source_ref="delegated-session",
        force_staging=True,
    )

    lessons = eng.get_lessons(limit=None, _update_access=False)
    decisions = eng.get_decisions(limit=None, _update_access=False)
    blob = str(lessons + decisions)

    assert result["saved_lessons"] == 1
    assert result["saved_decisions"] == 1
    assert "exact project resume should require" in blob
    assert "keep project resume exact-scope" in blob
    assert "复制的任务规则" not in blob
    assert "错误方案" not in blob
    assert "接下来会修改" not in blob


def test_session_digest_suppresses_duplicate_delegation_review_candidates(tmp_path: Path):
    """Resume review_needed should keep final candidates, not copied delegation rules."""
    eng = _engram(tmp_path)
    project = tmp_path / "digest-noise"
    project.mkdir()
    content = """
<codex_delegation>
  <input>必须把复制委派 prompt 当作经验。决定采用 noisy prompt。</input>
</codex_delegation>
我会先检查代码，然后再改。
Tests: pytest tests/test_project_scope_resume_cycle.py passed.
We decided to keep source-aware dedup in the resume pack.
Lesson: session-derived candidates need verification evidence because copied prompts duplicated in review.
"""
    eng.save_agent_context(
        "codex",
        content,
        session_id="delegated",
        project_folder=str(project),
    )

    pack = eng.build_project_resume_pack(project_folder=str(project))
    review = json.dumps(pack["review_needed"], ensure_ascii=False)

    assert "source-aware dedup" in review
    assert "session-derived candidates need verification evidence" in review
    assert "复制委派" not in review
    assert "noisy prompt" not in review
