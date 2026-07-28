"""Conservative filters for session-derived memory candidates."""

from __future__ import annotations

import re


_BLOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<codex_delegation\b.*?</codex_delegation>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<recommended_plugins\b.*?</recommended_plugins>", re.IGNORECASE | re.DOTALL),
    re.compile(r"```.*?```", re.DOTALL),
)

_PROCESS_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"i\s+(?:will|am|was|just|can|need|should)\b|"
    r"i'm\b|i’ll\b|"
    r"next\s+i\b|"
    r"now\s+i\b|"
    r"收到|好的|我(?:会|将|正在|现在|先|已经|刚才|接下来)|"
    r"正在|接下来|先(?:给|看|查|做)|"
    r"status\s+update|progress\s+update|heartbeat|"
    r"中间检查点|工具调用次数|使用的工具"
    r")",
    re.IGNORECASE,
)

_DELEGATION_MARKER_RE = re.compile(
    r"(?:"
    r"source_thread_id|codex_delegation|recommended_plugins|"
    r"agents\.md\s+instructions|developer\s+instructions|"
    r"system\s+message|approval\s+policy|sandbox_mode|"
    r"请作为|请先回复|范围边界|建议验收|真实使用中暴露"
    r")",
    re.IGNORECASE,
)

_USER_INSTRUCTION_RE = re.compile(
    r"^\s*(?:please\b|you\s+must\b|you\s+should\b|must\b|should\b|"
    r"请|必须|应该|不要|不得|禁止|范围|建议|要求)"
)

_UNCERTAIN_RE = re.compile(
    r"\b(?:maybe|consider|evaluate|explore|whether|should we|plan|planned)\b"
    r"|也许|考虑|评估|探索|是否|要不要|计划|建议",
    re.IGNORECASE,
)

_DECISION_SIGNAL_RE = re.compile(
    r"\b(?:we\s+decided|decided\s+to|final\s+decision|chose|selected|"
    r"switched\s+to|adopted)\b|"
    r"最终决定|明确决定|决定(?:使用|采用|保留|改为|改用)|"
    r"选择了|采用了|改为|改用",
    re.IGNORECASE,
)

_LESSON_EVIDENCE_RE = re.compile(
    r"\b(?:because|due\s+to|caused|caught|failed|passed|fixed|found\s+that|"
    r"learned\s+that|discovered\s+that|verified|tests?)\b|"
    r"因为|由于|导致|发现|修复|通过|失败|验证|测试|验收|踩坑|复盘|学到",
    re.IGNORECASE,
)

_EXPLICIT_LESSON_RE = re.compile(r"^\s*(?:lesson|lessons learned|经验|教训|复盘)\s*[:：]", re.IGNORECASE)


def strip_session_noise_blocks(text: str) -> str:
    """Remove copied prompts, quoted/code blocks, and delegation envelopes."""
    cleaned = str(text or "")
    for pattern in _BLOCK_PATTERNS:
        cleaned = pattern.sub("\n", cleaned)

    kept: list[str] = []
    in_xmlish_block = False
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith(("<input>", "<instructions>", "<environment_context>")):
            in_xmlish_block = True
            continue
        if lowered.startswith(("</input>", "</instructions>", "</environment_context>")):
            in_xmlish_block = False
            continue
        if in_xmlish_block:
            continue
        if line.startswith(">"):
            continue
        if _DELEGATION_MARKER_RE.search(line):
            continue
        kept.append(raw_line)
    return "\n".join(kept)


def is_process_or_delegation_sentence(sentence: str) -> bool:
    text = " ".join(str(sentence or "").strip().split())
    if not text:
        return True
    lowered = text.lower()
    if _DELEGATION_MARKER_RE.search(text):
        return True
    if _PROCESS_PREFIX_RE.search(text) and not (
        _DECISION_SIGNAL_RE.search(text) or _LESSON_EVIDENCE_RE.search(text)
    ):
        return True
    if _USER_INSTRUCTION_RE.search(lowered) and not (
        _DECISION_SIGNAL_RE.search(text) or _LESSON_EVIDENCE_RE.search(text)
    ):
        return True
    return False


def has_explicit_decision_signal(sentence: str) -> bool:
    text = " ".join(str(sentence or "").strip().split())
    if not text or _UNCERTAIN_RE.search(text):
        return False
    return bool(_DECISION_SIGNAL_RE.search(text))


def has_lesson_outcome_signal(sentence: str) -> bool:
    text = " ".join(str(sentence or "").strip().split())
    if not text:
        return False
    return bool(_EXPLICIT_LESSON_RE.search(text) or _LESSON_EVIDENCE_RE.search(text))
