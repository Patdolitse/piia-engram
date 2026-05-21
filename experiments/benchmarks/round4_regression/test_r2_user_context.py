"""R2: get_user_context consistency through Engram.generate_context()."""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from typing import Any

from engram_core.core import Engram


def seed_r2_engram(engram: Engram) -> dict[str, Any]:
    profile = {
        "role": "本地优先 AI 记忆系统开发者",
        "language": "中文和 English",
        "tech_stack": "Python, MCP, FastMCP, pytest, local-first storage",
        "description": "关注本地文件图谱、MCP 工具边界、真实验证证据和中文交付报告。",
    }
    engram.update_profile(profile)
    engram.update_preferences(
        {
            "work_patterns": {
                "verification": "先运行真实命令再下结论",
                "handoff_quality": "报告要写清验证状态和边界",
                "handoff": "用本地任务包保存上下文",
            },
            "communication": "默认中文，简洁说明证据和边界",
            "tool_preferences": {
                "Codex": "实现和验证",
                "Claude": "设计审阅",
                "Cursor": "局部代码导航",
            },
        }
    )

    lessons = [
        "Engram 回归测试必须用临时数据目录，不能污染真实 ~/.engram。",
        "报告里要区分浏览器证据、API 证据和代码静态检查。",
        "Open-web 搜索广度不足时必须写 limited_search_scope，不要包装成完整搜索。",
        "Windows PowerShell 读取 UTF-8 中文时可能显示 mojibake，报告文件仍应按 UTF-8 写入。",
        "支付迁移预演缺少恢复链路证明时应保持 NO-GO。",
        "动态 MCP 工具加载已证明服务端可变，但宿主刷新仍是外部约束。",
        "项目资产图第一版应该从路径、入口、构建产物开始，不要先做泛化知识图谱。",
        "网站法律页面内容不可改，只能做布局层面的视觉调整。",
        "发布包验证要同时检查安装器和便携包，不能只看一个 exe。",
        "后续重构不能覆盖前三轮 benchmark 的代码和结果。",
    ]
    for lesson in lessons:
        engram.add_lesson(lesson, domain="regression", source_tool="round4_seed")

    decisions = [
        {
            "question": "是否继续推进 MCP 协调器上线？",
            "choice": "放弃协调器上线，保留显式工具路径",
            "reasoning": "第三轮 DeepSeek 基准显示协调器语义鲁棒性不足。",
            "domain": "mcp",
        },
        {
            "question": "第四轮 benchmark 的定位是什么？",
            "choice": "作为已上线功能回归基准",
            "reasoning": "当前目标是发现默默坏掉的核心路径，而不是做新功能决策。",
            "domain": "testing",
        },
        {
            "question": "是否把第四轮阈值接入 CI 发布阻断？",
            "choice": "暂不接入 CI gate，只生成供 owner 审阅的人工回归报告",
            "reasoning": "本轮目标是先建立可信基线，避免在阈值稳定前阻断真实发布流程。",
            "domain": "release",
        },
    ]
    for decision in decisions:
        engram.add_decision(decision, source_tool="round4_seed")

    return {
        "profile": profile,
        "key_lesson": lessons[-1],
        "key_decision": decisions[-1],
        "lesson_count": len(lessons),
        "decision_count": len(decisions),
    }


def run_r2(judge: Any, call_count: int = 10) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="engram-r2-") as tmp:
        engram = Engram(root=Path(tmp) / "engram")
        seed = seed_r2_engram(engram)
        contexts = [engram.generate_context() for _ in range(call_count)]

    context_rows = []
    for index, context in enumerate(contexts, start=1):
        judgment = judge.judge_context_snapshot(
            f"R2-C{index:02d}",
            context,
            seed["profile"],
            {"lesson": seed["key_lesson"], "decision": seed["key_decision"]},
        )
        judgment = _guard_context_judgment(judgment, context)
        context_rows.append(
            {
                "index": index,
                "length": len(context),
                "sha256": _sha256(context),
                "judgment": judgment,
            }
        )

    matrix = [[1.0 if i == j else None for j in range(call_count)] for i in range(call_count)]
    pair_rows = []
    for left, right in itertools.combinations(range(call_count), 2):
        judgment = judge.judge_similarity(
            f"R2-S{left + 1:02d}-{right + 1:02d}",
            contexts[left],
            contexts[right],
        )
        similarity = float(judgment.get("similarity", 0.0))
        matrix[left][right] = similarity
        matrix[right][left] = similarity
        pair_rows.append(
            {
                "left": left + 1,
                "right": right + 1,
                "similarity": similarity,
                "reasoning": judgment.get("reasoning", ""),
            }
        )

    identity_complete = sum(1 for row in context_rows if row["judgment"].get("identity_complete"))
    key_lesson_mentioned = sum(1 for row in context_rows if row["judgment"].get("key_lesson_mentioned"))
    key_decision_mentioned = sum(1 for row in context_rows if row["judgment"].get("key_decision_mentioned"))
    key_knowledge_mentioned = sum(1 for row in context_rows if row["judgment"].get("key_knowledge_mentioned"))
    min_similarity = min((row["similarity"] for row in pair_rows), default=1.0)
    byte_identical = len({context for context in contexts}) == 1

    summary = {
        "call_count": call_count,
        "identity_complete": identity_complete,
        "key_lesson_mentioned": key_lesson_mentioned,
        "key_decision_mentioned": key_decision_mentioned,
        "key_knowledge_mentioned": key_knowledge_mentioned,
        "min_similarity": min_similarity,
        "byte_identical": byte_identical,
        "get_user_context_uses_llm": False,
        "passed": identity_complete == 10 and key_knowledge_mentioned >= 8 and min_similarity >= 0.85,
    }
    return {
        "call_count": call_count,
        "seed": seed,
        "context_rows": context_rows,
        "similarity_pairs": pair_rows,
        "similarity_matrix": matrix,
        "summary": summary,
    }


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _guard_context_judgment(judgment: dict[str, Any], context: str) -> dict[str, Any]:
    """Require copied context evidence for key-knowledge positives."""
    guarded = dict(judgment)
    lesson_quote = _clean_quote(guarded.get("key_lesson_quote"))
    decision_quote = _clean_quote(guarded.get("key_decision_quote"))

    if not lesson_quote or lesson_quote not in context:
        guarded["key_lesson_mentioned"] = False
    if not decision_quote or decision_quote not in context:
        guarded["key_decision_mentioned"] = False
    guarded["key_knowledge_mentioned"] = bool(guarded.get("key_lesson_mentioned")) and bool(
        guarded.get("key_decision_mentioned")
    )
    return guarded


def _clean_quote(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().strip('"').strip("'")
    if text.lower() in {"", "null", "none", "n/a"}:
        return ""
    return text
