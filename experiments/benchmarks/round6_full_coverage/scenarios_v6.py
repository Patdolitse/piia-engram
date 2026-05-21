"""Round 6 full-coverage scenarios for the 39-tool Engram MCP surface."""

from __future__ import annotations

from collections import Counter
from typing import Any


G1_TOOLS = [
    "get_user_context",
    "get_identity_card",
    "get_profile",
    "get_work_style",
    "get_preferences",
    "get_trust_boundaries",
    "get_quality_standards",
    "update_identity",
    "get_project_context",
    "list_projects",
    "save_project_snapshot",
    "get_domains",
]

G2_TOOLS = [
    "add_lesson",
    "add_decision",
    "extract_session_insights",
    "ingest_notes",
    "bulk_add_knowledge",
    "update_knowledge",
    "search_knowledge",
    "get_relevant_knowledge",
    "find_similar_knowledge",
    "get_knowledge_inheritance",
    "get_lessons",
    "get_decisions",
    "get_related_knowledge",
    "get_knowledge_overview",
    "export_knowledge_report",
]

G3_TOOLS = [
    "archive_knowledge",
    "merge_knowledge",
    "link_knowledge",
    "unlink_knowledge",
    "export_engram",
    "import_engram",
    "export_engram_to_openclaw",
    "import_engram_from_openclaw",
    "get_audit_log",
    "read_web_content",
    "wrap_up_session",
    "start_project",
]

ALL_EXPECTED_TOOLS = G1_TOOLS + G2_TOOLS + G3_TOOLS


def scenario(
    scenario_id: str,
    group: str,
    expected_tool: str,
    user_input: str,
    category: str | None = None,
    boundary_type: str | None = None,
) -> dict[str, Any]:
    return {
        "id": scenario_id,
        "test_group": group,
        "category": category or expected_tool,
        "boundary_type": boundary_type,
        "user_input": user_input,
        "expected_tool": expected_tool,
    }


G1_SCENARIOS: list[dict[str, Any]] = [
    scenario(
        "G1-USER-CONTEXT-01",
        "G1",
        "get_user_context",
        "新的 Codex 会话开始了，请先加载我的完整个性化上下文，包括身份、工作方式、偏好、质量标准和关键决策。",
    ),
    scenario(
        "G1-USER-CONTEXT-02",
        "G1",
        "get_user_context",
        "冷启动一下 Engram 上下文，让你知道我是谁、怎么工作、有哪些长期边界和项目习惯。",
    ),
    scenario(
        "G1-IDENTITY-CARD-01",
        "G1",
        "get_identity_card",
        "请导出一份可以发给另一个 AI 的 Markdown 身份卡，内容要精简可携带。",
    ),
    scenario(
        "G1-IDENTITY-CARD-02",
        "G1",
        "get_identity_card",
        "生成我的 AI identity card，不要全量冷启动上下文，只要可分享的身份卡片。",
    ),
    scenario(
        "G1-PROFILE-01",
        "G1",
        "get_profile",
        "只看我的基础身份画像：我是谁、主要在做什么、长期目标是什么。",
    ),
    scenario(
        "G1-PROFILE-02",
        "G1",
        "get_profile",
        "读取用户 profile，不需要工作偏好和项目历史，只要身份画像。",
    ),
    scenario(
        "G1-WORK-STYLE-01",
        "G1",
        "get_work_style",
        "查看我的工作方式和协作节奏，比如我喜欢怎样推进任务、怎样沟通。",
    ),
    scenario(
        "G1-WORK-STYLE-02",
        "G1",
        "get_work_style",
        "只读取 work style，不要偏好总表：我通常怎么和 Codex 配合工作？",
    ),
    scenario(
        "G1-PREFERENCES-01",
        "G1",
        "get_preferences",
        "读取我的工具偏好、沟通风格和工作模式偏好。",
    ),
    scenario(
        "G1-PREFERENCES-02",
        "G1",
        "get_preferences",
        "我有哪些 preferences？包括喜欢本地文件、验证方式、工具选择这些偏好。",
    ),
    scenario(
        "G1-TRUST-BOUNDARIES-01",
        "G1",
        "get_trust_boundaries",
        "查看我的数据信任边界：哪些工具能访问哪些 Engram 数据，哪些不能碰。",
    ),
    scenario(
        "G1-TRUST-BOUNDARIES-02",
        "G1",
        "get_trust_boundaries",
        "读取隐私和工具访问边界，确认外部服务、Reader、MCP 能用到哪些数据。",
    ),
    scenario(
        "G1-QUALITY-01",
        "G1",
        "get_quality_standards",
        "读取我的质量标准和验收条件，尤其是完成前必须验证的规则。",
    ),
    scenario(
        "G1-QUALITY-02",
        "G1",
        "get_quality_standards",
        "我对报告、测试证据、不能过度声称这些质量要求是什么？",
    ),
    scenario(
        "G1-UPDATE-IDENTITY-01",
        "G1",
        "update_identity",
        "把我的质量标准更新一下：以后必须先给出可复现证据，再声称任务完成。",
    ),
    scenario(
        "G1-UPDATE-IDENTITY-02",
        "G1",
        "update_identity",
        "请修改我的工作偏好：遇到任务包时先读任务包和 git 状态，再执行。",
    ),
    scenario(
        "G1-PROJECT-CONTEXT-01",
        "G1",
        "get_project_context",
        "读取 E:/Personal Intelligence Identity Asset/engram 这个项目的历史上下文和项目快照。",
    ),
    scenario(
        "G1-PROJECT-CONTEXT-02",
        "G1",
        "get_project_context",
        "只加载 D:/Presentpad/project/website 的项目级上下文，不要加载我的全局用户身份。",
    ),
    scenario(
        "G1-LIST-PROJECTS-01",
        "G1",
        "list_projects",
        "列出 Engram 里记录过的所有项目档案和基本信息。",
    ),
    scenario(
        "G1-LIST-PROJECTS-02",
        "G1",
        "list_projects",
        "我现在有哪些已保存的项目？请给出项目列表。",
    ),
    scenario(
        "G1-SAVE-SNAPSHOT-01",
        "G1",
        "save_project_snapshot",
        "保存 E:/Personal Intelligence Identity Asset/engram 的项目快照，包含入口文件、测试命令和已知问题。",
    ),
    scenario(
        "G1-SAVE-SNAPSHOT-02",
        "G1",
        "save_project_snapshot",
        "把当前仓库的技术栈、构建命令、验证命令写入项目档案快照。",
    ),
    scenario(
        "G1-DOMAINS-01",
        "G1",
        "get_domains",
        "查看我的技术领域经验图谱，看看我在哪些 domain 上积累最多。",
    ),
    scenario(
        "G1-DOMAINS-02",
        "G1",
        "get_domains",
        "列出 Engram 里已有的领域标签和对应经验覆盖情况。",
    ),
]


G2_SCENARIOS: list[dict[str, Any]] = [
    scenario(
        "G2-ADD-LESSON-01",
        "G2",
        "add_lesson",
        "记一条经验教训：DeepSeek 工具选择评估必须保存 raw request/response，否则报告没有复核证据。",
    ),
    scenario(
        "G2-ADD-LESSON-02",
        "G2",
        "add_lesson",
        "新增 lesson：Windows PowerShell 读 UTF-8 任务包时要显式指定编码，避免中文乱码。",
    ),
    scenario(
        "G2-ADD-DECISION-01",
        "G2",
        "add_decision",
        "记录关键决策：Engram v3.7.0 暂时保留 39 个原子工具，不上线五工具 coordinator。",
    ),
    scenario(
        "G2-ADD-DECISION-02",
        "G2",
        "add_decision",
        "我们决定 Round 6 只做验证，不修改 mcp_server.py 主代码。",
    ),
    scenario(
        "G2-EXTRACT-SESSION-01",
        "G2",
        "extract_session_insights",
        "从这段会话总结里自动提取 lessons 和 decisions 并写入 Engram：今天验证了 R2 修复，发现 identity card 也需要包含 decisions。",
    ),
    scenario(
        "G2-EXTRACT-SESSION-02",
        "G2",
        "extract_session_insights",
        "请分析本轮对话摘要，把里面值得沉淀的经验和关键决策自动分类保存。",
    ),
    scenario(
        "G2-INGEST-NOTES-01",
        "G2",
        "ingest_notes",
        "把下面这段会议笔记导入知识库，自动抽取经验与决策：团队讨论了工具描述优化、边界场景和后续基线。",
    ),
    scenario(
        "G2-INGEST-NOTES-02",
        "G2",
        "ingest_notes",
        "我有一批自由文本笔记，里面混着事实、教训和决策，请解析后写入 Engram。",
    ),
    scenario(
        "G2-BULK-ADD-01",
        "G2",
        "bulk_add_knowledge",
        "一次性写入这三条知识：lesson A、lesson B、decision C，不要逐条调用。",
    ),
    scenario(
        "G2-BULK-ADD-02",
        "G2",
        "bulk_add_knowledge",
        "批量添加 5 条已经整理好的 lessons/decisions，它们都有 summary、detail 和 domain。",
    ),
    scenario(
        "G2-UPDATE-KNOWLEDGE-01",
        "G2",
        "update_knowledge",
        "把 lesson-2026-round5-start-project 的 detail 更新为：快捷工具容易抢走只读继承场景。",
    ),
    scenario(
        "G2-UPDATE-KNOWLEDGE-02",
        "G2",
        "update_knowledge",
        "更新 decision-2026-tool-surface 的 reasoning，补充 Round 6 全覆盖验证结果。",
    ),
    scenario(
        "G2-SEARCH-01",
        "G2",
        "search_knowledge",
        "搜索关键词 DeepSeek 工具选择 稳定性，找相关经验和决策。",
    ),
    scenario(
        "G2-SEARCH-02",
        "G2",
        "search_knowledge",
        "按关键词查一下 Python 编码乱码 相关的知识条目。",
    ),
    scenario(
        "G2-RELEVANT-01",
        "G2",
        "get_relevant_knowledge",
        "当前项目路径是 E:/Personal Intelligence Identity Asset/engram，请自动推荐最相关的历史经验，不提供搜索词。",
    ),
    scenario(
        "G2-RELEVANT-02",
        "G2",
        "get_relevant_knowledge",
        "根据 D:/Presentpad/project/website 这个路径，给我推荐可能有帮助的项目经验。",
    ),
    scenario(
        "G2-SIMILAR-01",
        "G2",
        "find_similar_knowledge",
        "根据 lesson-2026-round5-tool-shortcut 这条已有知识，找相似内容。",
    ),
    scenario(
        "G2-SIMILAR-02",
        "G2",
        "find_similar_knowledge",
        "找出和 decision-2026-keep-atomic-tools 相似的决策或经验条目。",
    ),
    scenario(
        "G2-INHERITANCE-01",
        "G2",
        "get_knowledge_inheritance",
        "我准备做一个新网页审计任务，只想拿可继承的跨项目经验，不要创建项目档案或保存快照。",
    ),
    scenario(
        "G2-INHERITANCE-02",
        "G2",
        "get_knowledge_inheritance",
        "给这个新研究方向生成知识继承包：相关 lessons、decisions、风险提示即可，不要初始化项目。",
    ),
    scenario(
        "G2-GET-LESSONS-01",
        "G2",
        "get_lessons",
        "列出 testing 领域最近 10 条经验教训。",
    ),
    scenario(
        "G2-GET-LESSONS-02",
        "G2",
        "get_lessons",
        "只看 lessons 列表，按 backend domain 过滤。",
    ),
    scenario(
        "G2-GET-DECISIONS-01",
        "G2",
        "get_decisions",
        "列出 architecture 领域的关键决策和推理。",
    ),
    scenario(
        "G2-GET-DECISIONS-02",
        "G2",
        "get_decisions",
        "我想回顾最近做过的 decisions，不要 lessons。",
    ),
    scenario(
        "G2-RELATED-01",
        "G2",
        "get_related_knowledge",
        "查看 lesson-2026-r2-fix 关联的所有知识条目。",
    ),
    scenario(
        "G2-RELATED-02",
        "G2",
        "get_related_knowledge",
        "根据 decision-2026-round6-baseline 的链接关系，列出相关 lessons 和 decisions。",
    ),
    scenario(
        "G2-OVERVIEW-01",
        "G2",
        "get_knowledge_overview",
        "生成知识库总览，包含 digest、健康状态、重复项和陈旧项。",
    ),
    scenario(
        "G2-OVERVIEW-02",
        "G2",
        "get_knowledge_overview",
        "整体看一下 Engram 知识库是否有 stale items 和重复知识，不要执行合并。",
    ),
    scenario(
        "G2-EXPORT-REPORT-01",
        "G2",
        "export_knowledge_report",
        "导出一份完整 Markdown 知识报告到默认 exports 目录，并返回报告内容。",
    ),
    scenario(
        "G2-EXPORT-REPORT-02",
        "G2",
        "export_knowledge_report",
        "生成 full knowledge report，方便我离线审阅所有 lessons 和 decisions。",
    ),
]


G3_SCENARIOS: list[dict[str, Any]] = [
    scenario(
        "G3-ARCHIVE-01",
        "G3",
        "archive_knowledge",
        "归档 lesson-2026-old-duplicate，这条经验已经过时但不要删除。",
    ),
    scenario(
        "G3-ARCHIVE-02",
        "G3",
        "archive_knowledge",
        "把 decision-2025-temp-plan 标记为 archived，后续不要默认检索出来。",
    ),
    scenario(
        "G3-MERGE-01",
        "G3",
        "merge_knowledge",
        "把 lesson-duplicate-2 合并进 lesson-duplicate-1，并保留主条目的 ID。",
    ),
    scenario(
        "G3-MERGE-02",
        "G3",
        "merge_knowledge",
        "这两条知识重复了，请将 secondary_id=decision-b 合并到 primary_id=decision-a。",
    ),
    scenario(
        "G3-LINK-01",
        "G3",
        "link_knowledge",
        "把 lesson-encoding-utf8 和 decision-use-explicit-python 建立双向关联。",
    ),
    scenario(
        "G3-LINK-02",
        "G3",
        "link_knowledge",
        "给 lesson-round6-coverage 关联 decision-keep-39-tools，表示它们互相支撑。",
    ),
    scenario(
        "G3-UNLINK-01",
        "G3",
        "unlink_knowledge",
        "取消 lesson-old 和 decision-unrelated 之间的关联。",
    ),
    scenario(
        "G3-UNLINK-02",
        "G3",
        "unlink_knowledge",
        "移除这两个知识条目的双向 link：lesson-a 与 lesson-b。",
    ),
    scenario(
        "G3-EXPORT-ENGRAM-01",
        "G3",
        "export_engram",
        "把整个 Engram 导出成一个内部备份文件。",
    ),
    scenario(
        "G3-EXPORT-ENGRAM-02",
        "G3",
        "export_engram",
        "生成 Engram JSON 备份，用于本地恢复，不是 OpenClaw 格式。",
    ),
    scenario(
        "G3-IMPORT-ENGRAM-01",
        "G3",
        "import_engram",
        "从 C:/backup/engram-backup.json 导入 Engram 数据。",
    ),
    scenario(
        "G3-IMPORT-ENGRAM-02",
        "G3",
        "import_engram",
        "恢复一个之前导出的内部 Engram 备份文件。",
    ),
    scenario(
        "G3-EXPORT-OPENCLAW-01",
        "G3",
        "export_engram_to_openclaw",
        "导出到 OpenClaw 兼容格式，生成 SOUL.md、MEMORY.md 和 USER.md。",
    ),
    scenario(
        "G3-EXPORT-OPENCLAW-02",
        "G3",
        "export_engram_to_openclaw",
        "把 Engram 转成 OpenClaw 文件夹结构，output_dir 用默认值即可。",
    ),
    scenario(
        "G3-IMPORT-OPENCLAW-01",
        "G3",
        "import_engram_from_openclaw",
        "从一个 OpenClaw 目录导入 SOUL.md/MEMORY.md/USER.md 到 Engram。",
    ),
    scenario(
        "G3-IMPORT-OPENCLAW-02",
        "G3",
        "import_engram_from_openclaw",
        "把旧 OpenClaw 知识包迁移进 Engram。",
    ),
    scenario(
        "G3-AUDIT-01",
        "G3",
        "get_audit_log",
        "查看最近 20 条 Engram 审计日志。",
    ),
    scenario(
        "G3-AUDIT-02",
        "G3",
        "get_audit_log",
        "列出最近的写入和导入操作记录，方便我排查是谁改了知识库。",
    ),
    scenario(
        "G3-READ-WEB-01",
        "G3",
        "read_web_content",
        "读取 https://example.com/article 这篇网页的正文内容并返回摘要。",
    ),
    scenario(
        "G3-READ-WEB-02",
        "G3",
        "read_web_content",
        "用 Engram Reader 抽取这个视频链接的文本内容：https://example.com/video",
    ),
    scenario(
        "G3-WRAP-SESSION-01",
        "G3",
        "wrap_up_session",
        "本轮会话结束，请自动提取经验和决策，并保存当前项目快照作为收尾。",
    ),
    scenario(
        "G3-WRAP-SESSION-02",
        "G3",
        "wrap_up_session",
        "收尾一下今天的工作：沉淀会话知识，同时更新 E:/Personal Intelligence Identity Asset/engram 的项目档案。",
    ),
    scenario(
        "G3-START-PROJECT-01",
        "G3",
        "start_project",
        "启动一个新的本地 AI harness 项目，请继承相关历史经验并创建项目档案。",
    ),
    scenario(
        "G3-START-PROJECT-02",
        "G3",
        "start_project",
        "我刚进入一个全新仓库，需要根据项目描述拉取可继承知识，同时建立项目快照。",
    ),
]


G4_SCENARIOS: list[dict[str, Any]] = [
    scenario(
        "G4-NONE-01",
        "G4",
        "none",
        "你好，今天天气怎么样？",
        category="no_tool",
        boundary_type="no_tool",
    ),
    scenario(
        "G4-NONE-02",
        "G4",
        "none",
        "Engram 是什么？给我解释一下。",
        category="no_tool",
        boundary_type="no_tool",
    ),
    scenario(
        "G4-NONE-03",
        "G4",
        "none",
        "我刚才说的那个方案，你觉得有道理吗？",
        category="no_tool",
        boundary_type="no_tool",
    ),
    scenario(
        "G4-NONE-04",
        "G4",
        "none",
        "等一下，我还在想。",
        category="no_tool",
        boundary_type="no_tool",
    ),
    scenario(
        "G4-NONE-05",
        "G4",
        "none",
        "谢谢你的帮助！",
        category="no_tool",
        boundary_type="no_tool",
    ),
    scenario(
        "G4-MISSING-01",
        "G4",
        "add_lesson",
        "帮我记一下这个教训。",
        category="missing_params",
        boundary_type="missing_params",
    ),
    scenario(
        "G4-MISSING-02",
        "G4",
        "search_knowledge",
        "搜索一下相关经验。",
        category="missing_params",
        boundary_type="missing_params",
    ),
    scenario(
        "G4-MISSING-03",
        "G4",
        "merge_knowledge",
        "合并这两条知识。",
        category="missing_params",
        boundary_type="missing_params",
    ),
    scenario(
        "G4-MISSING-04",
        "G4",
        "export_engram_to_openclaw",
        "导出到 OpenClaw。",
        category="missing_params",
        boundary_type="missing_params",
    ),
    scenario(
        "G4-MISSING-05",
        "G4",
        "save_project_snapshot",
        "保存项目快照。",
        category="missing_params",
        boundary_type="missing_params",
    ),
]


SCENARIOS_V6 = G1_SCENARIOS + G2_SCENARIOS + G3_SCENARIOS + G4_SCENARIOS
GROUP_SCENARIOS = {
    "g1": G1_SCENARIOS,
    "g2": G2_SCENARIOS,
    "g3": G3_SCENARIOS,
    "g4": G4_SCENARIOS,
}


def validate_scenarios_v6(scenarios: list[dict[str, Any]] = SCENARIOS_V6) -> None:
    expected_counts = {"G1": 24, "G2": 30, "G3": 24, "G4": 10}
    counts = Counter(item["test_group"] for item in scenarios)
    if dict(counts) != expected_counts:
        raise ValueError(f"Unexpected Round 6 group counts: {dict(counts)}")

    ids = [item["id"] for item in scenarios]
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"Duplicate scenario ids: {duplicates}")

    for item in scenarios:
        missing = {"id", "test_group", "user_input", "expected_tool", "category"} - set(item)
        if missing:
            raise ValueError(f"{item.get('id', '<unknown>')} missing {sorted(missing)}")

    for group_name, group_scenarios, tools in (
        ("G1", G1_SCENARIOS, G1_TOOLS),
        ("G2", G2_SCENARIOS, G2_TOOLS),
        ("G3", G3_SCENARIOS, G3_TOOLS),
    ):
        per_tool = Counter(item["expected_tool"] for item in group_scenarios)
        expected = {tool: 2 for tool in tools}
        if dict(per_tool) != expected:
            raise ValueError(f"Unexpected {group_name} per-tool counts: {dict(per_tool)}")

    g4_counts = Counter(item["boundary_type"] for item in G4_SCENARIOS)
    if dict(g4_counts) != {"no_tool": 5, "missing_params": 5}:
        raise ValueError(f"Unexpected G4 boundary counts: {dict(g4_counts)}")

    covered = set(item["expected_tool"] for item in G1_SCENARIOS + G2_SCENARIOS + G3_SCENARIOS)
    missing_tools = sorted(set(ALL_EXPECTED_TOOLS) - covered)
    if missing_tools:
        raise ValueError(f"Missing tool coverage: {missing_tools}")
