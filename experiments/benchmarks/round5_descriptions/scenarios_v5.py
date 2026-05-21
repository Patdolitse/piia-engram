"""Round 5 scenarios for tool-description and shortcut verification."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from experiments.benchmarks.round3.scenarios_v3 import SCENARIOS_V3


ROUND3_E_BY_ID = {
    scenario["id"]: scenario
    for scenario in SCENARIOS_V3
    if scenario["test_group"] == "E"
}
ROUND3_BY_ID = {scenario["id"]: scenario for scenario in SCENARIOS_V3}


def _from_round3_e(scenario_id: str) -> dict[str, Any]:
    source = deepcopy(ROUND3_E_BY_ID[scenario_id])
    return {
        "id": source["id"],
        "test_group": "T1",
        "source": "round3_e",
        "group": source["near_synonym_group"],
        "user_input": source["user_input"],
        "expected_tool": source["expected_atomic_tool"],
    }


def _from_round3_core(scenario_id: str, category: str) -> dict[str, Any]:
    source = deepcopy(ROUND3_BY_ID[scenario_id])
    return {
        "id": f"T3-{source['id']}",
        "round3_id": source["id"],
        "test_group": "T3",
        "source": "round3_core",
        "category": category,
        "user_input": source["user_input"],
        "expected_tool": source["expected_atomic_tool"],
    }


T1_SCENARIOS: list[dict[str, Any]] = [
    _from_round3_e("E01"),
    _from_round3_e("E02"),
    _from_round3_e("E03"),
    _from_round3_e("E04"),
    _from_round3_e("E05"),
    _from_round3_e("E06"),
    _from_round3_e("E07"),
    _from_round3_e("E08"),
    _from_round3_e("E09"),
    _from_round3_e("E10"),
    {
        "id": "T1-LDE-05",
        "test_group": "T1",
        "source": "round5_new",
        "group": "lesson_decision_extract",
        "user_input": "记录一条经验：不要把临时演示数据写进真实用户的 Engram 目录。",
        "expected_tool": "add_lesson",
    },
    {
        "id": "T1-LDE-06",
        "test_group": "T1",
        "source": "round5_new",
        "group": "lesson_decision_extract",
        "user_input": "提炼成一条 lesson：LLM judge 的正例必须带可复制的证据 quote。",
        "expected_tool": "add_lesson",
    },
    {
        "id": "T1-LDE-07",
        "test_group": "T1",
        "source": "round5_new",
        "group": "lesson_decision_extract",
        "user_input": "我们最终决定保持 37 个原子工具，不上线五工具协调器。",
        "expected_tool": "add_decision",
    },
    {
        "id": "T1-LDE-08",
        "test_group": "T1",
        "source": "round5_new",
        "group": "lesson_decision_extract",
        "user_input": "下面是一整段会话摘要，请自动分析并保存里面可能存在的经验和决策。",
        "expected_tool": "extract_session_insights",
    },
    {
        "id": "T1-SRS-04",
        "test_group": "T1",
        "source": "round5_new",
        "group": "search_relevant_similar",
        "user_input": "搜索一下和 DeepSeek 评估稳定性有关的经验。",
        "expected_tool": "search_knowledge",
    },
    {
        "id": "T1-SRS-05",
        "test_group": "T1",
        "source": "round5_new",
        "group": "search_relevant_similar",
        "user_input": "当前项目路径是 E:/Personal Intelligence Identity Asset/engram，给我自动推荐最相关的历史经验。",
        "expected_tool": "get_relevant_knowledge",
    },
    {
        "id": "T1-SRS-06",
        "test_group": "T1",
        "source": "round5_new",
        "group": "search_relevant_similar",
        "user_input": "根据 decision-2026-round4-r2 这条已有知识，找出相似的条目。",
        "expected_tool": "find_similar_knowledge",
    },
    {
        "id": "T1-SCU-04",
        "test_group": "T1",
        "source": "round5_new",
        "group": "snapshot_context_user",
        "user_input": "把这个项目的入口文件、构建命令和已知问题写入项目快照。",
        "expected_tool": "save_project_snapshot",
    },
    {
        "id": "T1-SCU-05",
        "test_group": "T1",
        "source": "round5_new",
        "group": "snapshot_context_user",
        "user_input": "只读取 E:/Presentpad/project/website 的项目历史，不要加载我的全局身份卡。",
        "expected_tool": "get_project_context",
    },
    {
        "id": "T1-SCU-06",
        "test_group": "T1",
        "source": "round5_new",
        "group": "snapshot_context_user",
        "user_input": "新开一个 AI 对话，先加载我的身份、工作偏好、质量标准和关键决策。",
        "expected_tool": "get_user_context",
    },
]


T2_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "T2-W01",
        "test_group": "T2",
        "group": "wrap_up_session",
        "user_input": "这轮会话结束了，请把摘要里的经验和决策保存，并顺手更新当前项目快照。",
        "expected_tool": "wrap_up_session",
    },
    {
        "id": "T2-W02",
        "test_group": "T2",
        "group": "wrap_up_session",
        "user_input": "收尾一下今天的工作：提取 lessons/decisions，同时保存 project_folder 的最新状态。",
        "expected_tool": "wrap_up_session",
    },
    {
        "id": "T2-W03",
        "test_group": "T2",
        "group": "wrap_up_session",
        "user_input": "请结束本次 Codex 协作，把会话总结沉淀到 Engram，并更新这个项目的 title 和 tech_stack。",
        "expected_tool": "wrap_up_session",
    },
    {
        "id": "T2-W04",
        "test_group": "T2",
        "group": "wrap_up_session",
        "user_input": "把本次修复验证的总结、发现、项目快照一次性保存，作为会话收尾。",
        "expected_tool": "wrap_up_session",
    },
    {
        "id": "T2-W05",
        "test_group": "T2",
        "group": "wrap_up_session",
        "user_input": "我不想分别调用提取和保存快照，直接做会话收尾并更新项目档案。",
        "expected_tool": "wrap_up_session",
    },
    {
        "id": "T2-S01",
        "test_group": "T2",
        "group": "start_project",
        "user_input": "我要启动一个新的本地优先知识库项目，请继承过往相关经验并创建项目档案。",
        "expected_tool": "start_project",
    },
    {
        "id": "T2-S02",
        "test_group": "T2",
        "group": "start_project",
        "user_input": "新建一个 Android 发布流程项目，先找可继承的 lessons 和 decisions，再保存项目快照。",
        "expected_tool": "start_project",
    },
    {
        "id": "T2-S03",
        "test_group": "T2",
        "group": "start_project",
        "user_input": "开始做一个 web-mvp 信任审计工具，帮我初始化项目记录并拉取相关历史经验。",
        "expected_tool": "start_project",
    },
    {
        "id": "T2-S04",
        "test_group": "T2",
        "group": "start_project",
        "user_input": "我刚进入一个全新的仓库，需要基于项目描述继承跨项目知识，同时建立项目快照。",
        "expected_tool": "start_project",
    },
    {
        "id": "T2-S05",
        "test_group": "T2",
        "group": "start_project",
        "user_input": "给这个新项目开档：根据描述推荐旧经验，设置项目路径、标题和技术栈。",
        "expected_tool": "start_project",
    },
]


T3_SCENARIOS: list[dict[str, Any]] = [
    _from_round3_core("S01", "remember"),
    _from_round3_core("S04", "remember"),
    _from_round3_core("S14", "recall"),
    _from_round3_core("S18", "inherit"),
    _from_round3_core("S21", "sync"),
    _from_round3_core("S24", "cleanup"),
    _from_round3_core("S27", "advanced"),
    _from_round3_core("S28", "advanced"),
    _from_round3_core("S29", "advanced"),
    _from_round3_core("S30", "advanced"),
    _from_round3_core("E01", "remember"),
    _from_round3_core("E05", "project"),
    _from_round3_core("E06", "project"),
    _from_round3_core("E09", "recall"),
    _from_round3_core("E10", "cleanup"),
]


SCENARIOS_V5 = T1_SCENARIOS + T2_SCENARIOS + T3_SCENARIOS


def validate_scenarios_v5(scenarios: list[dict[str, Any]] = SCENARIOS_V5) -> None:
    expected_counts = {"T1": 20, "T2": 10, "T3": 15}
    counts = {key: 0 for key in expected_counts}
    ids = set()
    for scenario in scenarios:
        missing = {"id", "test_group", "user_input", "expected_tool"} - set(scenario)
        if missing:
            raise ValueError(f"{scenario.get('id', '<unknown>')} missing {sorted(missing)}")
        if scenario["id"] in ids:
            raise ValueError(f"duplicate scenario id: {scenario['id']}")
        ids.add(scenario["id"])
        counts[scenario["test_group"]] = counts.get(scenario["test_group"], 0) + 1
    if counts != expected_counts:
        raise ValueError(f"Unexpected Round 5 counts: {counts}")

    t1_groups = {}
    for scenario in T1_SCENARIOS:
        t1_groups[scenario["group"]] = t1_groups.get(scenario["group"], 0) + 1
    if t1_groups != {
        "lesson_decision_extract": 8,
        "search_relevant_similar": 6,
        "snapshot_context_user": 6,
    }:
        raise ValueError(f"Unexpected T1 groups: {t1_groups}")
