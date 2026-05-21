"""Human-reviewed dialogue snippets for extract_session_insights regression."""

from __future__ import annotations

from typing import Any


EXTRACTION_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "R3-L01",
        "category": "lesson",
        "dialogue": (
            "We found a recurring issue in the Windows packaging flow. The installer can look correct "
            "while the portable zip still contains the old binary. Remember to verify both artifacts "
            "before publishing a release."
        ),
    },
    {
        "id": "R3-L02",
        "category": "lesson",
        "dialogue": (
            "今天复盘 web-mvp 的搜索页面，发现 raw_result_count 和 unique_company_count 必须分开记录，"
            "否则用户会误以为搜索广度比实际更大。"
        ),
    },
    {
        "id": "R3-L03",
        "category": "lesson",
        "dialogue": (
            "The fixture looked harmless, but it masked the real API timeout. Lesson learned: keep "
            "network fakes explicit and label them in the report."
        ),
    },
    {
        "id": "R3-L04",
        "category": "lesson",
        "dialogue": (
            "注意：Engram 这类本地知识库测试一定要用临时目录，不能直接写真实 ~/.engram，"
            "不然回归测试会污染用户数据。"
        ),
    },
    {
        "id": "R3-L05",
        "category": "lesson",
        "dialogue": (
            "We learned that the browser evidence is only valid after a real page interaction. "
            "A code-only inspection should be reported separately from a UX pass."
        ),
    },
    {
        "id": "R3-D01",
        "category": "decision",
        "dialogue": (
            "After comparing the coordinator prototype with the existing MCP tools, we decided to "
            "drop the coordinator launch path for now and keep the 37 explicit tools."
        ),
    },
    {
        "id": "R3-D02",
        "category": "decision",
        "dialogue": (
            "这次支付迁移预演选择 NO-GO：在 OSS 上传、下载、解密、恢复链路没有重新证明前，"
            "不开放生产 PayPal 切换。"
        ),
    },
    {
        "id": "R3-D03",
        "category": "decision",
        "dialogue": (
            "We selected a local-first project asset graph instead of a generic vector database, "
            "because the immediate need is stable file geography and build outputs."
        ),
    },
    {
        "id": "R3-D04",
        "category": "decision",
        "dialogue": (
            "今天决定把第四轮 benchmark 定位成已上线功能回归，而不是继续讨论协调器是否上线。"
        ),
    },
    {
        "id": "R3-D05",
        "category": "decision",
        "dialogue": (
            "The team switched the Android verification target from a mocked API run to a real emulator "
            "install, because version confirmation matters more than a local build log."
        ),
    },
    {
        "id": "R3-O01",
        "category": "ordinary",
        "dialogue": (
            "I checked the dashboard after lunch. The cards loaded in the same order as yesterday, "
            "and the meeting notes are in the shared folder."
        ),
    },
    {
        "id": "R3-O02",
        "category": "ordinary",
        "dialogue": (
            "我们明天上午看一下新图标，今天先把截图放在临时文件夹里，方便设计工具继续处理。"
            "这只是一次素材整理，没有形成新的规则或取舍。"
        ),
    },
    {
        "id": "R3-O03",
        "category": "ordinary",
        "dialogue": (
            "The export took around two minutes on my laptop. I am going to rerun it later when the "
            "network is quieter."
        ),
    },
    {
        "id": "R3-O04",
        "category": "ordinary",
        "dialogue": (
            "这个任务现在没有新结论，只是把昨天的三份文档重新打开确认了一遍，内容都还在。"
            "后面如果继续推进，再单独写正式决策记录。"
        ),
    },
    {
        "id": "R3-O05",
        "category": "ordinary",
        "dialogue": (
            "We should look at the chart labels tomorrow, but there is no product decision today. "
            "For now it is only a visual review note."
        ),
    },
]


def validate_extraction_scenarios(scenarios: list[dict[str, Any]]) -> None:
    """Validate the fixed R3 scenario inventory."""
    if len(scenarios) != 15:
        raise ValueError("R3 must contain exactly 15 dialogue snippets")
    counts = {"lesson": 0, "decision": 0, "ordinary": 0}
    seen: set[str] = set()
    for scenario in scenarios:
        if scenario.get("id") in seen:
            raise ValueError(f"duplicate R3 id: {scenario.get('id')}")
        seen.add(str(scenario.get("id")))
        category = scenario.get("category")
        if category not in counts:
            raise ValueError(f"invalid R3 category: {category}")
        counts[category] += 1
        if len(str(scenario.get("dialogue", "")).strip()) < 40:
            raise ValueError(f"{scenario.get('id')} dialogue is too short")
    if counts != {"lesson": 5, "decision": 5, "ordinary": 5}:
        raise ValueError(f"R3 category counts must be 5/5/5, got {counts}")
