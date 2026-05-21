"""Onboarding seed scenarios for round 4 regression tests."""

from __future__ import annotations

from typing import Any


ONBOARDING_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "R1-ZH-BACKEND",
        "role": "后端开发者",
        "tech_stack": "Python, FastAPI, pytest, PostgreSQL",
        "language": "中文",
        "language_group": "zh",
        "language_groups": ["zh"],
        "primary_search_query": "python pytest",
        "lessons": [
            "Python 后端项目里，修改数据库迁移前必须先跑 pytest，并确认回滚路径。",
            "FastAPI 接口变更要同步更新 OpenAPI 示例，避免客户端拿到旧字段。",
        ],
    },
    {
        "id": "R1-EN-FRONTEND",
        "role": "Frontend developer",
        "tech_stack": "TypeScript, React, Vite, Playwright",
        "language": "English",
        "language_group": "en",
        "language_groups": ["en"],
        "primary_search_query": "typescript playwright",
        "lessons": [
            "When changing TypeScript UI state, run Playwright smoke tests before claiming the flow works.",
            "Keep React component props narrow so dashboard panels do not re-render unrelated sections.",
        ],
    },
    {
        "id": "R1-ZH-FULLSTACK-MULTI",
        "role": "全栈开发者",
        "tech_stack": "Python, TypeScript, React, Postgres",
        "language": "中文和 English",
        "language_group": "multi",
        "language_groups": ["zh", "en"],
        "primary_search_query": "postgres react",
        "lessons": [
            "全栈改动要先确认 API 合约，再改 React 页面，避免前后端字段漂移。",
            "For multilingual projects, keep user-facing copy in the existing locale files instead of hardcoding strings.",
        ],
    },
    {
        "id": "R1-EN-DATA",
        "role": "Data science developer",
        "tech_stack": "Python, pandas, scikit-learn, Jupyter",
        "language": "English",
        "language_group": "en",
        "language_groups": ["en"],
        "primary_search_query": "python pandas",
        "lessons": [
            "Before trusting a pandas cleaning step, persist the row-count delta and sample rejected rows.",
            "Model evaluation reports should include the baseline metric, not just the tuned score.",
        ],
    },
    {
        "id": "R1-ZH-CROSS-TOOL",
        "role": "跨工具开发者",
        "tech_stack": "Claude Code, Codex, Cursor, MCP, Python",
        "language": "中文",
        "language_group": "zh",
        "language_groups": ["zh"],
        "primary_search_query": "codex mcp",
        "lessons": [
            "Claude、Codex、Cursor 之间交接时，要留下本地任务包和验证证据，避免上下文断裂。",
            "MCP 工具设计要先证明最小可用路径，再扩大工具数量。",
        ],
    },
]


def validate_onboarding_scenarios(scenarios: list[dict[str, Any]]) -> None:
    """Validate the fixed R1 scenario inventory."""
    if len(scenarios) != 5:
        raise ValueError("R1 must contain exactly 5 onboarding scenarios")
    required = {
        "id",
        "role",
        "tech_stack",
        "language",
        "language_group",
        "language_groups",
        "primary_search_query",
        "lessons",
    }
    seen: set[str] = set()
    for scenario in scenarios:
        missing = required - set(scenario)
        if missing:
            raise ValueError(f"{scenario.get('id', '<unknown>')} missing fields: {sorted(missing)}")
        if scenario["id"] in seen:
            raise ValueError(f"duplicate R1 id: {scenario['id']}")
        seen.add(scenario["id"])
        if scenario["language_group"] not in {"zh", "en", "multi"}:
            raise ValueError(f"invalid language_group: {scenario['language_group']}")
        if not scenario["lessons"]:
            raise ValueError(f"{scenario['id']} must include at least one lesson")

