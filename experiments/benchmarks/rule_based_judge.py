"""Rule-based evaluator used when a real LLM judge is unavailable.

This is not claimed to be actual Claude/Cursor behavior. It is a transparent,
deterministic proxy that uses natural-language intent patterns without reading
the expected labels from the scenario dataset.
"""

from __future__ import annotations

import re


def choose_atomic_tool(user_input: str) -> str:
    """Select one atomic Engram MCP tool from the user's request text."""
    text = _normalize(user_input)

    if _is_audit_log_request(text):
        return "get_audit_log"
    if _contains_any(text, ("手动合并", "合并 lesson", "合并 decision", "merge")):
        return "merge_knowledge"
    if _contains_any(text, ("导出", "export")):
        if _contains_any(text, ("报告", "report")):
            return "export_knowledge_report"
        return "export_engram"
    if _contains_any(text, ("导入", "import")):
        return "import_engram"
    if _is_identity_update_request(text):
        return "update_identity"

    if _is_sync_request(text):
        return "get_user_context"
    if _is_cleanup_request(text):
        return "get_knowledge_overview"
    if _is_inherit_request(text):
        return "get_knowledge_inheritance"
    if _is_recall_request(text):
        return "search_knowledge"
    if _is_lesson_request(text):
        return "add_lesson"
    if _is_decision_request(text):
        return "add_decision"

    return "search_knowledge"


def choose_coordinator_tool(user_input: str) -> str:
    """Select one high-level coordinator action, or fallback for advanced tools."""
    text = _normalize(user_input)

    if _is_advanced_request(text):
        return "fallback"
    if _is_sync_request(text):
        return "sync"
    if _is_cleanup_request(text):
        return "cleanup"
    if _is_inherit_request(text):
        return "inherit"
    if _is_recall_request(text):
        return "recall"
    if _is_lesson_request(text) or _is_decision_request(text):
        return "remember"

    return "recall"


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker.lower() in text for marker in markers)


def _is_sync_request(text: str) -> bool:
    return _contains_any(text, ("新对话", "冷启动", "加载我的上下文", "刚进入这个仓库", "先同步"))


def _is_cleanup_request(text: str) -> bool:
    return _contains_any(
        text,
        (
            "重复的经验",
            "重复的 lesson",
            "可能重复",
            "stale",
            "清理",
            "整理一下重复",
            "合并的知识项候选",
            "review plan",
        ),
    )


def _is_inherit_request(text: str) -> bool:
    return _contains_any(text, ("新项目", "新的", "开始一个", "继承", "参考一下", "过去项目的经验", "旧项目经验"))


def _is_recall_request(text: str) -> bool:
    return _contains_any(text, ("找一下", "搜一下", "搜索一下", "有没有", "之前", "怎么判断", "提取流程", "回忆"))


def _is_lesson_request(text: str) -> bool:
    lesson_markers = (
        "刚发现",
        "踩坑",
        "经验",
        "学到了",
        "复盘",
        "线上问题",
        "会导致",
        "失败",
        "信号",
        "教训",
    )
    return _contains_any(text, lesson_markers)


def _is_decision_request(text: str) -> bool:
    decision_markers = (
        "决定",
        "选择",
        "决策",
        "以后",
        "统一",
        "固定为",
        "不再",
        "采用",
        "长期事实源",
    )
    if _contains_any(text, ("不代表我们已经做了某个决策", "不是要现在选择某个方案")):
        return False
    return _contains_any(text, decision_markers) or bool(re.search(r"\bprefer\b|\buse\b", text))


def _is_advanced_request(text: str) -> bool:
    return (
        _contains_any(text, ("导出", "导入", "openclaw"))
        or _contains_any(text, ("手动合并", "合并 lesson", "合并 decision", "merge"))
        or _is_audit_log_request(text)
        or _is_identity_update_request(text)
    )


def _is_audit_log_request(text: str) -> bool:
    return _contains_any(text, ("审计日志", "audit log")) or (
        _contains_any(text, ("审计", "audit")) and _contains_any(text, ("查看", "最近"))
    )


def _is_identity_update_request(text: str) -> bool:
    return _contains_any(text, ("quality_standards", "质量标准", "工作偏好", "身份画像")) and _contains_any(
        text, ("更新", "改成", "写成")
    )
