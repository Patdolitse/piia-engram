"""Dedicated unit tests for session_filters (conservative candidate filters).

Previously exercised only indirectly through extraction-quality tests; the
block filters, process-prefix heuristic, delegation markers, and decision /
lesson evidence signals each get direct coverage here, including the
XML-envelope state machine and fail-closed defaults.
"""
from __future__ import annotations

from piia_engram import session_filters as sf


# ── strip_session_noise_blocks ──────────────────────────────────────────────


def test_strips_code_fences_completely_including_unclosed_tail():
    text = "before\n```python\nSECRET_IN_FENCE = 1\n```\nafter"
    assert "SECRET_IN_FENCE" not in sf.strip_session_noise_blocks(text)
    assert "before" in sf.strip_session_noise_blocks(text)
    assert "after" in sf.strip_session_noise_blocks(text)


def test_strips_known_xml_envelopes_and_line_blocks():
    text = (
        "<codex_delegation>do the thing</codex_delegation>\n"
        "<input>\nline inside input\n</input>\n"
        "<instructions>\nline inside instructions\n</instructions>\n"
        "<environment_context>\nenv line\n</environment_context>\n"
        "> quoted line\n"
        "kept line\n"
    )
    out = sf.strip_session_noise_blocks(text)
    assert "kept line" in out
    for gone in (
        "do the thing", "line inside input", "line inside instructions",
        "env line", "quoted line",
    ):
        assert gone not in out


def test_delegation_marker_lines_are_dropped():
    text = "normal line\nsource_thread_id: abc\nagents.md instructions here\nkept"
    out = sf.strip_session_noise_blocks(text)
    assert "normal line" in out and "kept" in out
    assert "abc" not in out
    assert "agents.md instructions" not in out


# ── is_process_or_delegation_sentence ───────────────────────────────────────


def test_empty_sentence_is_process_noise():
    assert sf.is_process_or_delegation_sentence("") is True
    assert sf.is_process_or_delegation_sentence("   ") is True


def test_process_prefixes_are_noise_without_evidence_or_decision():
    assert sf.is_process_or_delegation_sentence("I will now run the build") is True
    assert sf.is_process_or_delegation_sentence("收到，我先看一下") is True
    assert sf.is_process_or_delegation_sentence("status update: still working") is True


def test_evidence_word_overrides_process_prefix_by_design():
    # "tests" is a lesson-evidence word: a process-prefixed sentence that
    # mentions testing is treated as signal-bearing, not noise.
    assert sf.is_process_or_delegation_sentence("I will now run the tests") is False


def test_process_prefix_with_decision_signal_is_kept():
    assert sf.is_process_or_delegation_sentence(
        "I will keep the linear rebase because the branch protection forbids merge commits"
    ) is False


def test_user_instruction_prefix_is_noise_unless_backed_by_signal():
    assert sf.is_process_or_delegation_sentence("please rerun everything") is True
    assert sf.is_process_or_delegation_sentence("必须先验证再发布，因为上次失败") is False


# ── has_explicit_decision_signal / has_lesson_outcome_signal ────────────────


def test_decision_signal_accepts_stable_phrases():
    assert sf.has_explicit_decision_signal("we decided to adopt the queue") is True
    assert sf.has_explicit_decision_signal("最终决定采用线性方案") is True


def test_decision_signal_rejects_uncertain_phrasing():
    assert sf.has_explicit_decision_signal("maybe we should consider the queue") is False
    assert sf.has_explicit_decision_signal("是否要改用新方案还没定") is False


def test_lesson_evidence_signal_accepts_stable_phrases():
    assert sf.has_lesson_outcome_signal("发现 CI 挂了因为路径解析错") is True
    assert sf.has_lesson_outcome_signal("verified locally before pushing") is True
    assert sf.has_lesson_outcome_signal("教训：不要在周五下午发版") is True


def test_lesson_evidence_signal_rejects_plain_statements():
    assert sf.has_lesson_outcome_signal("the build ran fine") is False
    assert sf.has_lesson_outcome_signal("") is False
