"""D3 LLM: Recall Quality — 1 LLM evaluation test.

Use DeepSeek to judge whether retrieved lessons are relevant
to the project context.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from engram_core.core import Engram

from .fixtures import RECALL_LESSONS, create_lessons_only
from .llm_judge import DeepSeekJudge, build_relevance_rating_prompt


def run_d3_llm(judge: DeepSeekJudge) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="r10-d3llm-") as tmp:
        e = create_lessons_only(Path(tmp), RECALL_LESSONS)
        e.save_project_snapshot("E:/proj", {
            "title": "Engram MCP Server",
            "tech_stack": ["Python", "MCP", "FastMCP"],
        })
        lessons = e.get_relevant_lessons(
            project_folder="E:/proj", limit=8, _update_access=False
        )

    project_desc = "一个 Python 编写的 MCP Server 项目，使用 FastMCP 框架，提供个人知识管理功能。"
    system, user = build_relevance_rating_prompt(lessons, project_desc)

    def _extract(raw: str) -> float:
        """Extract average relevance score from JSON response."""
        try:
            # Try to parse JSON from the response
            raw_clean = raw.strip()
            if raw_clean.startswith("```"):
                raw_clean = raw_clean.split("\n", 1)[1].rsplit("```", 1)[0]
            ratings = json.loads(raw_clean)
            scores = [r.get("score", 0) for r in ratings]
            if scores:
                # Fraction of items rated >= 1 (relevant or highly relevant)
                return sum(1 for s in scores if s >= 1) / len(scores)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return 0.0

    relevance_ratio = judge.majority_vote(system, user, extractor=_extract)
    ok = relevance_ratio >= 0.75

    return {
        "dimension": "D3_llm",
        "name": "Recall Quality (LLM)",
        "total": 1,
        "correct": 1 if ok else 0,
        "passed": ok,
        "results": [{
            "id": "D3-QUALITY-01",
            "correct": ok,
            "detail": f"relevance_ratio={relevance_ratio:.0%}, threshold=75%",
        }],
    }
