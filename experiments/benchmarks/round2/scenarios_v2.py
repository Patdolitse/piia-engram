"""Round 2 fixed scenario set.

Total shape follows the task package's hard total:
- 30 scenarios reused from round 1 and marked as test D
- 10 synonym rewrite scenarios for test C, two groups of five
- 10 near-synonym atomic-tool confusion scenarios for test E
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from experiments.benchmarks.scenarios import REQUIRED_KEYS, SCENARIOS


ROUND2_REQUIRED_KEYS = REQUIRED_KEYS | {"test_group"}


SYNONYM_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "C01",
        "category": "remember_lesson",
        "user_input": "踩坑：npm 和 pnpm 混用以后 lockfile 很容易漂移",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "javascript",
        "difficulty": "medium",
        "test_group": "C",
        "synonym_group": "lesson_lockfile",
    },
    {
        "id": "C02",
        "category": "remember_lesson",
        "user_input": "复盘一下，前端依赖管理工具选择不一致会让 CI 装出不同版本",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "javascript",
        "difficulty": "hard",
        "test_group": "C",
        "synonym_group": "lesson_lockfile",
    },
    {
        "id": "C03",
        "category": "remember_lesson",
        "user_input": "经验：package-lock 和 pnpm-lock 同时存在时，依赖树会变得不可预测",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "javascript",
        "difficulty": "medium",
        "test_group": "C",
        "synonym_group": "lesson_lockfile",
    },
    {
        "id": "C04",
        "category": "remember_lesson",
        "user_input": "学到一点：选择 npm 还是 pnpm 要统一，不然锁文件会互相打架",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "javascript",
        "difficulty": "hard",
        "test_group": "C",
        "synonym_group": "lesson_lockfile",
    },
    {
        "id": "C05",
        "category": "remember_lesson",
        "user_input": "记个教训，Node 项目别让两个包管理器轮流生成锁文件",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "javascript",
        "difficulty": "medium",
        "test_group": "C",
        "synonym_group": "lesson_lockfile",
    },
    {
        "id": "C06",
        "category": "remember_decision",
        "user_input": "决定：这个仓库后续测试统一用 pytest",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "medium",
        "test_group": "C",
        "synonym_group": "decision_pytest",
    },
    {
        "id": "C07",
        "category": "remember_decision",
        "user_input": "以后新增 Python 测试都走 pytest，不再扩 unittest",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "hard",
        "test_group": "C",
        "synonym_group": "decision_pytest",
    },
    {
        "id": "C08",
        "category": "remember_decision",
        "user_input": "测试框架就定 pytest 了，unittest 只维护旧用例",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "hard",
        "test_group": "C",
        "synonym_group": "decision_pytest",
    },
    {
        "id": "C09",
        "category": "remember_decision",
        "user_input": "Python 这边选择 pytest 作为默认测试入口",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "medium",
        "test_group": "C",
        "synonym_group": "decision_pytest",
    },
    {
        "id": "C10",
        "category": "remember_decision",
        "user_input": "从今天起测试命令固定成 pytest，不再让多个框架并行",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "hard",
        "test_group": "C",
        "synonym_group": "decision_pytest",
    },
]


NEAR_SYNONYM_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "E01",
        "category": "near_synonym",
        "user_input": "把这段会话里的经验和决策自动提取出来，沉淀到 Engram",
        "expected_atomic_tool": "extract_session_insights",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
        "test_group": "E",
        "near_synonym_group": "lesson_decision_extract",
    },
    {
        "id": "E02",
        "category": "near_synonym",
        "user_input": "记录一条经验：Windows 控制台编码会影响 Python 上传包",
        "expected_atomic_tool": "add_lesson",
        "expected_coordinator_tool": "remember",
        "expected_kind": "lesson",
        "expected_domain": "python",
        "difficulty": "medium",
        "test_group": "E",
        "near_synonym_group": "lesson_decision_extract",
    },
    {
        "id": "E03",
        "category": "near_synonym",
        "user_input": "记录决策：以后这个项目都用 pytest 做测试",
        "expected_atomic_tool": "add_decision",
        "expected_coordinator_tool": "remember",
        "expected_kind": "decision",
        "expected_domain": "testing",
        "difficulty": "medium",
        "test_group": "E",
        "near_synonym_group": "lesson_decision_extract",
    },
    {
        "id": "E04",
        "category": "near_synonym",
        "user_input": "把下面的会议纪要批量分析出 lessons 和 decisions",
        "expected_atomic_tool": "extract_session_insights",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
        "test_group": "E",
        "near_synonym_group": "lesson_decision_extract",
    },
    {
        "id": "E05",
        "category": "near_synonym",
        "user_input": "保存当前项目快照，包含入口、构建命令和产物路径",
        "expected_atomic_tool": "save_project_snapshot",
        "expected_coordinator_tool": "fallback",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
        "test_group": "E",
        "near_synonym_group": "snapshot_context_user",
    },
    {
        "id": "E06",
        "category": "near_synonym",
        "user_input": "读取这个项目的历史项目上下文，不要加载完整用户身份",
        "expected_atomic_tool": "get_project_context",
        "expected_coordinator_tool": "recall",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
        "test_group": "E",
        "near_synonym_group": "snapshot_context_user",
    },
    {
        "id": "E07",
        "category": "near_synonym",
        "user_input": "新对话开始，先加载用户上下文和工作偏好",
        "expected_atomic_tool": "get_user_context",
        "expected_coordinator_tool": "sync",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
        "test_group": "E",
        "near_synonym_group": "snapshot_context_user",
    },
    {
        "id": "E08",
        "category": "near_synonym",
        "user_input": "帮我找一下关于鉴权失败的经验",
        "expected_atomic_tool": "search_knowledge",
        "expected_coordinator_tool": "recall",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "medium",
        "test_group": "E",
        "near_synonym_group": "search_relevant_similar",
    },
    {
        "id": "E09",
        "category": "near_synonym",
        "user_input": "根据当前项目路径给我最相关的跨项目知识",
        "expected_atomic_tool": "get_relevant_knowledge",
        "expected_coordinator_tool": "inherit",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
        "test_group": "E",
        "near_synonym_group": "search_relevant_similar",
    },
    {
        "id": "E10",
        "category": "near_synonym",
        "user_input": "找到和 lesson-123 相似的知识项，方便我判断要不要合并",
        "expected_atomic_tool": "find_similar_knowledge",
        "expected_coordinator_tool": "cleanup",
        "expected_kind": None,
        "expected_domain": None,
        "difficulty": "hard",
        "test_group": "E",
        "near_synonym_group": "search_relevant_similar",
    },
]


def _round1_scenarios() -> list[dict[str, Any]]:
    scenarios = deepcopy(SCENARIOS)
    for scenario in scenarios:
        scenario["test_group"] = "D"
    return scenarios


SCENARIOS_V2: list[dict[str, Any]] = _round1_scenarios() + SYNONYM_SCENARIOS + NEAR_SYNONYM_SCENARIOS


def validate_scenarios_v2(scenarios: list[dict[str, Any]]) -> None:
    if len(scenarios) != 50:
        raise ValueError(f"Expected 50 scenarios, got {len(scenarios)}")

    ids = [scenario.get("id") for scenario in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("Scenario IDs must be unique")

    groups = Counter(scenario.get("test_group") for scenario in scenarios)
    expected_groups = {"D": 30, "C": 10, "E": 10}
    if groups != expected_groups:
        raise ValueError(f"Expected groups {expected_groups}, got {dict(groups)}")

    for scenario in scenarios:
        missing = ROUND2_REQUIRED_KEYS - set(scenario)
        if missing:
            raise ValueError(f"{scenario.get('id', '<unknown>')} missing keys: {sorted(missing)}")
        if scenario["test_group"] == "C" and not scenario.get("synonym_group"):
            raise ValueError(f"{scenario['id']} needs synonym_group")
        if scenario["test_group"] == "E" and not scenario.get("near_synonym_group"):
            raise ValueError(f"{scenario['id']} needs near_synonym_group")

    synonym_counts = Counter(
        scenario["synonym_group"]
        for scenario in scenarios
        if scenario["test_group"] == "C"
    )
    if synonym_counts != {"lesson_lockfile": 5, "decision_pytest": 5}:
        raise ValueError(f"Invalid synonym grouping: {dict(synonym_counts)}")

    near_counts = Counter(
        scenario["near_synonym_group"]
        for scenario in scenarios
        if scenario["test_group"] == "E"
    )
    if near_counts != {
        "lesson_decision_extract": 4,
        "snapshot_context_user": 3,
        "search_relevant_similar": 3,
    }:
        raise ValueError(f"Invalid near-synonym grouping: {dict(near_counts)}")
