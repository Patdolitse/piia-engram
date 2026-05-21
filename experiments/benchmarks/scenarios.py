"""Scenario dataset for MCP tool-selection benchmark.

The scenarios are intentionally phrased as ordinary user requests instead of
labels. Evaluators must infer the intended tool from the input text.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


REQUIRED_KEYS = {
    "id",
    "category",
    "user_input",
    "expected_atomic_tool",
    "expected_coordinator_tool",
    "expected_kind",
    "expected_domain",
    "difficulty",
}

VALID_CATEGORIES = {
    "remember_lesson",
    "remember_decision",
    "recall",
    "cleanup",
    "inherit",
    "sync",
    "advanced",
}

VALID_DIFFICULTIES = {"easy", "medium", "hard"}

VALID_COORDINATOR_TOOLS = {"remember", "recall", "cleanup", "inherit", "sync", "fallback"}


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "S01",
        "category": "remember_lesson",
        "user_input": "刚发现 Windows 上 Python 默认用 GBK 编码会让 twine 上传失败",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "python",
        "difficulty": "easy",
    },
    {
        "id": "S02",
        "category": "remember_lesson",
        "user_input": "踩坑：npm 和 pnpm 混用会让锁文件漂移，CI 里依赖版本对不上",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "javascript",
        "difficulty": "easy",
    },
    {
        "id": "S03",
        "category": "remember_lesson",
        "user_input": "经验：MCP 工具描述如果写得太泛，模型会把 search 和 recall 混用",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "mcp",
        "difficulty": "easy",
    },
    {
        "id": "S04",
        "category": "remember_decision",
        "user_input": "决定这个项目用 PostgreSQL 存审计事件，不再放在普通日志里",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "database",
        "difficulty": "easy",
    },
    {
        "id": "S05",
        "category": "remember_decision",
        "user_input": "我们选择 TypeScript 而不是 JavaScript 做浏览器扩展",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "frontend",
        "difficulty": "easy",
    },
    {
        "id": "S06",
        "category": "remember_decision",
        "user_input": "决策：CI 统一走 GitHub Actions，先不接第三方流水线",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "devops",
        "difficulty": "easy",
    },
    {
        "id": "S07",
        "category": "remember_lesson",
        "user_input": "用户决定取消订阅通常是 churn 的前置信号，不代表我们已经做了某个决策",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "product",
        "difficulty": "hard",
    },
    {
        "id": "S08",
        "category": "remember_lesson",
        "user_input": "学到了：API 限流策略选择不当会导致雪崩，不是要现在选择某个方案",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "backend",
        "difficulty": "hard",
    },
    {
        "id": "S09",
        "category": "remember_lesson",
        "user_input": "复盘：路径图谱里把安装位置写错，会让 AI 重复搜索浪费 token",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "project_context",
        "difficulty": "hard",
    },
    {
        "id": "S10",
        "category": "remember_lesson",
        "user_input": "线上问题：测试框架名字写对了但命令路径错了，pytest 根本没跑到目标用例",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "testing",
        "difficulty": "hard",
    },
    {
        "id": "S11",
        "category": "remember_decision",
        "user_input": "以后这个仓库都用 pytest，不再写 unittest 新用例",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "hard",
    },
    {
        "id": "S12",
        "category": "remember_decision",
        "user_input": "这个项目的长期事实源固定为本地 JSON，云端只做可选备份",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "architecture",
        "difficulty": "hard",
    },
    {
        "id": "S13",
        "category": "remember_decision",
        "user_input": "发布包统一从 mainline release pack 产出，维护包只做隔离验证",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "release",
        "difficulty": "hard",
    },
    {
        "id": "S14",
        "category": "recall",
        "user_input": "帮我找一下关于鉴权的经验",
        "expected_atomic_tool": "search_knowledge",
        "expected_coordinator_tool": "recall",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S15",
        "category": "recall",
        "user_input": "之前 PayPal 和人民币定价我们是怎么判断的？",
        "expected_atomic_tool": "search_knowledge",
        "expected_coordinator_tool": "recall",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S16",
        "category": "recall",
        "user_input": "找一下 PIIA Reader API 的提取流程和失败回退方式",
        "expected_atomic_tool": "search_knowledge",
        "expected_coordinator_tool": "recall",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S17",
        "category": "recall",
        "user_input": "有没有关于不要伪造搜索广度的教训？",
        "expected_atomic_tool": "search_knowledge",
        "expected_coordinator_tool": "recall",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S18",
        "category": "inherit",
        "user_input": "我开始一个新的电商项目，给我点过去项目的经验",
        "expected_atomic_tool": "get_knowledge_inheritance",
        "expected_coordinator_tool": "inherit",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S19",
        "category": "inherit",
        "user_input": "我要做本地优先写作工具，看看哪些旧项目经验可以继承",
        "expected_atomic_tool": "get_knowledge_inheritance",
        "expected_coordinator_tool": "inherit",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S20",
        "category": "inherit",
        "user_input": "新的安卓发布流程，参考一下 PresentPad 踩过的坑",
        "expected_atomic_tool": "get_knowledge_inheritance",
        "expected_coordinator_tool": "inherit",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S21",
        "category": "sync",
        "user_input": "新对话开始，加载我的上下文",
        "expected_atomic_tool": "get_user_context",
        "expected_coordinator_tool": "sync",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "easy",
    },
    {
        "id": "S22",
        "category": "sync",
        "user_input": "Codex 刚进入这个仓库，先冷启动用户身份和项目上下文",
        "expected_atomic_tool": "get_user_context",
        "expected_coordinator_tool": "sync",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "easy",
    },
    {
        "id": "S23",
        "category": "sync",
        "user_input": "先同步我的工作偏好、质量标准和当前项目状态",
        "expected_atomic_tool": "get_user_context",
        "expected_coordinator_tool": "sync",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S24",
        "category": "cleanup",
        "user_input": "整理一下重复的经验，不要自动合并",
        "expected_atomic_tool": "get_knowledge_overview",
        "expected_coordinator_tool": "cleanup",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S25",
        "category": "cleanup",
        "user_input": "检查 stale 的知识和可能重复的 lesson",
        "expected_atomic_tool": "get_knowledge_overview",
        "expected_coordinator_tool": "cleanup",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S26",
        "category": "cleanup",
        "user_input": "帮我列出可以合并的知识项候选，先给 review plan",
        "expected_atomic_tool": "get_knowledge_overview",
        "expected_coordinator_tool": "cleanup",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S27",
        "category": "advanced",
        "user_input": "导出 Engram 数据为 JSON",
        "expected_atomic_tool": "export_engram",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S28",
        "category": "advanced",
        "user_input": "手动合并 lesson-123 和 lesson-456",
        "expected_atomic_tool": "merge_knowledge",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S29",
        "category": "advanced",
        "user_input": "查看最近 20 条审计日志",
        "expected_atomic_tool": "get_audit_log",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
    },
    {
        "id": "S30",
        "category": "advanced",
        "user_input": "把 quality_standards 里的验收条件更新成先验证再声称完成",
        "expected_atomic_tool": "update_identity",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
    },
]


def validate_scenarios(scenarios: list[dict[str, Any]]) -> None:
    """Raise ValueError if the benchmark dataset violates the task contract."""
    if len(scenarios) != 30:
        raise ValueError(f"Expected 30 scenarios, got {len(scenarios)}")

    ids = [scenario.get("id") for scenario in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("Scenario IDs must be unique")

    for scenario in scenarios:
        missing = REQUIRED_KEYS - set(scenario)
        if missing:
            raise ValueError(f"{scenario.get('id', '<unknown>')} missing keys: {sorted(missing)}")
        if scenario["category"] not in VALID_CATEGORIES:
            raise ValueError(f"{scenario['id']} has invalid category {scenario['category']!r}")
        if scenario["difficulty"] not in VALID_DIFFICULTIES:
            raise ValueError(f"{scenario['id']} has invalid difficulty {scenario['difficulty']!r}")
        if scenario["expected_coordinator_tool"] not in VALID_COORDINATOR_TOOLS:
            raise ValueError(
                f"{scenario['id']} has invalid coordinator expectation "
                f"{scenario['expected_coordinator_tool']!r}"
            )
        if scenario["expected_coordinator_tool"] == "remember":
            if scenario["expected_kind"] not in {"lesson", "decision"}:
                raise ValueError(f"{scenario['id']} remember scenario needs expected_kind")
            if not scenario["expected_domain"]:
                raise ValueError(f"{scenario['id']} remember scenario needs expected_domain")

    counts = Counter(scenario["category"] for scenario in scenarios)
    for category in VALID_CATEGORIES:
        if counts[category] < 3:
            raise ValueError(f"Category {category!r} needs at least 3 scenarios")

    hard_count = sum(1 for scenario in scenarios if scenario["difficulty"] == "hard")
    if hard_count < 5:
        raise ValueError("Dataset needs at least 5 hard scenarios")

