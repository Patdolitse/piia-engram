"""D4: Identity Fidelity — 5 LLM evaluation tests.

Inject generate_context() into DeepSeek system prompt, then verify
the AI can correctly answer questions about the user.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .fixtures import create_full_engram
from .llm_judge import DeepSeekJudge, build_context_qa_prompt


def run_d4(judge: DeepSeekJudge) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="r10-d4-") as tmp:
        e = create_full_engram(Path(tmp))
        context = e.generate_context(project_folder="E:/test-project")

    scenarios = [
        ("D4-ROLE-01",
         "这个用户的技术水平如何？他的角色是什么？",
         ["senior", "高级", "全栈", "工程师"]),
        ("D4-PREF-01",
         "给这个用户写代码评审意见时应该用什么沟通风格？",
         ["简洁", "直接", "不要废话"]),
        ("D4-QUALITY-01",
         "用户会接受没有测试覆盖的代码吗？",
         ["不会", "不接受", "必须", "测试"]),
        ("D4-DECISION-01",
         "用户之前决定用什么测试框架？",
         ["pytest"]),
        ("D4-LESSON-01",
         "用户在 MCP 开发中踩过什么坑？请列举至少一个。",
         ["同步阻塞", "MCP", "tool description", "同义词"]),
    ]

    results = []
    for case_id, question, expected_keywords in scenarios:
        system, user_q = build_context_qa_prompt(context, question)

        def _extract(raw: str) -> bool:
            return any(kw in raw for kw in expected_keywords)

        answer_ok = judge.majority_vote(system, user_q, extractor=_extract)
        # Get last raw answer for detail
        raw_answer = judge.ask(system, user_q)
        results.append({
            "id": case_id,
            "correct": bool(answer_ok),
            "detail": f"expected_any_of={expected_keywords}, answer_snippet={raw_answer[:100]}",
        })

    return {
        "dimension": "D4",
        "name": "Identity Fidelity (LLM)",
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "passed": sum(1 for r in results if r["correct"]) >= 4,
        "results": results,
    }
