"""Round 3 scenarios: round2 set plus keyword-replacement variants."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from experiments.benchmarks.round2.scenarios_v2 import SCENARIOS_V2, validate_scenarios_v2


ROUND3_REQUIRED_KEYS = {
    "id",
    "category",
    "user_input",
    "expected_atomic_tool",
    "expected_coordinator_tool",
    "expected_kind",
    "expected_domain",
    "difficulty",
    "test_group",
    "variant_of",
    "original_keywords",
}


KEYWORD_VARIANTS: list[dict[str, Any]] = [
    {
        "id": "F01",
        "variant_of": "S01",
        "original_keywords": ["GBK", "twine"],
        "user_input": "在 Windows 环境下，CPython 解释器的默认编码让 PyPI 上传脚本崩了",
        "synonym_group": "lesson_incident_variant",
    },
    {
        "id": "F02",
        "variant_of": "S02",
        "original_keywords": ["npm", "pnpm", "锁文件"],
        "user_input": "前端依赖如果混着两套包管理器维护，版本约束文件会漂移，流水线装出来不一致",
        "synonym_group": "lesson_incident_variant",
    },
    {
        "id": "F03",
        "variant_of": "S08",
        "original_keywords": ["API", "限流", "雪崩"],
        "user_input": "学到一个后端事故规律：请求节流方案配置错了，会把下游服务一起拖垮",
        "synonym_group": "lesson_incident_variant",
    },
    {
        "id": "F04",
        "variant_of": "S09",
        "original_keywords": ["路径图谱", "安装位置", "token"],
        "user_input": "项目资产地图里如果把工具落盘目录记错，助手会反复重新定位，浪费上下文预算",
        "synonym_group": "lesson_incident_variant",
    },
    {
        "id": "F05",
        "variant_of": "S10",
        "original_keywords": ["pytest", "目标用例"],
        "user_input": "线上复盘：测试入口字符串看似正确，但实际命令没有覆盖该跑的检查项",
        "synonym_group": "lesson_incident_variant",
    },
    {
        "id": "F06",
        "variant_of": "S04",
        "original_keywords": ["PostgreSQL", "审计事件"],
        "user_input": "这个系统的操作追踪数据以后落到关系型数据库，不再混进普通运行日志",
        "synonym_group": "decision_stack_variant",
    },
    {
        "id": "F07",
        "variant_of": "S05",
        "original_keywords": ["TypeScript", "JavaScript"],
        "user_input": "浏览器插件代码栈定成带类型检查的脚本语言，不用纯动态脚本继续扩展",
        "synonym_group": "decision_stack_variant",
    },
    {
        "id": "F08",
        "variant_of": "S06",
        "original_keywords": ["GitHub Actions", "第三方流水线"],
        "user_input": "持续集成入口统一放在代码托管平台自带的自动化里，暂时不接外部构建服务",
        "synonym_group": "decision_stack_variant",
    },
    {
        "id": "F09",
        "variant_of": "S11",
        "original_keywords": ["pytest", "unittest"],
        "user_input": "Python 测试体系就收敛到一个轻量测试运行器，旧标准库框架只维护存量",
        "synonym_group": "decision_stack_variant",
    },
    {
        "id": "F10",
        "variant_of": "S12",
        "original_keywords": ["本地 JSON", "云端"],
        "user_input": "长期事实的主存储放在本机结构化文本里，远端同步只作为可选副本",
        "synonym_group": "decision_stack_variant",
    },
    {
        "id": "F11",
        "variant_of": "S14",
        "original_keywords": ["找一下", "鉴权"],
        "user_input": "帮我翻一翻以前关于登录校验失败的经验记录",
        "synonym_group": "recall_auth_variant",
    },
    {
        "id": "F12",
        "variant_of": "S15",
        "original_keywords": ["PayPal", "人民币定价"],
        "user_input": "查一下我们之前对海外支付和本地币种价格展示的判断",
        "synonym_group": "recall_auth_variant",
    },
    {
        "id": "F13",
        "variant_of": "S16",
        "original_keywords": ["PIIA Reader API", "提取流程"],
        "user_input": "回忆一下本地内容读取服务的抓取链路和失败回退",
        "synonym_group": "recall_auth_variant",
    },
    {
        "id": "F14",
        "variant_of": "S17",
        "original_keywords": ["不要伪造搜索广度"],
        "user_input": "有没有沉淀过关于搜索范围不能夸大的经验",
        "synonym_group": "recall_auth_variant",
    },
    {
        "id": "F15",
        "variant_of": "E08",
        "original_keywords": ["鉴权失败"],
        "user_input": "查一下认证流程出错时我们以前踩过什么坑",
        "synonym_group": "recall_auth_variant",
    },
]


def _base_scenarios() -> list[dict[str, Any]]:
    scenarios = deepcopy(SCENARIOS_V2)
    for scenario in scenarios:
        scenario["variant_of"] = None
        scenario["original_keywords"] = []
    return scenarios


def _variant_scenarios(base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {scenario["id"]: scenario for scenario in base}
    variants: list[dict[str, Any]] = []
    for spec in KEYWORD_VARIANTS:
        original = deepcopy(by_id[spec["variant_of"]])
        original.update(
            {
                "id": spec["id"],
                "user_input": spec["user_input"],
                "test_group": "F",
                "variant_of": spec["variant_of"],
                "original_keywords": spec["original_keywords"],
                "synonym_group": spec["synonym_group"],
            }
        )
        original.pop("near_synonym_group", None)
        variants.append(original)
    return variants


SCENARIOS_V3: list[dict[str, Any]] = _base_scenarios()
SCENARIOS_V3.extend(_variant_scenarios(SCENARIOS_V3))


def validate_scenarios_v3(scenarios: list[dict[str, Any]]) -> None:
    validate_scenarios_v2([scenario for scenario in scenarios if scenario["test_group"] != "F"])
    if len(scenarios) != 65:
        raise ValueError(f"Expected 65 scenarios, got {len(scenarios)}")
    ids = [scenario.get("id") for scenario in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("Scenario IDs must be unique")
    by_id = {scenario["id"]: scenario for scenario in scenarios}

    for scenario in scenarios:
        missing = ROUND3_REQUIRED_KEYS - set(scenario)
        if missing:
            raise ValueError(f"{scenario.get('id', '<unknown>')} missing keys: {sorted(missing)}")
        if scenario["test_group"] == "F":
            if scenario["variant_of"] not in by_id:
                raise ValueError(f"{scenario['id']} variant_of points to missing scenario")
            if not scenario["original_keywords"]:
                raise ValueError(f"{scenario['id']} needs original_keywords")

    groups = Counter(scenario["test_group"] for scenario in scenarios)
    if groups != {"D": 30, "C": 10, "E": 10, "F": 15}:
        raise ValueError(f"Unexpected test_group counts: {dict(groups)}")

    synonym_counts = Counter(
        scenario["synonym_group"]
        for scenario in scenarios
        if scenario.get("synonym_group")
    )
    for group in (
        "lesson_lockfile",
        "decision_pytest",
        "lesson_incident_variant",
        "decision_stack_variant",
        "recall_auth_variant",
    ):
        if synonym_counts[group] != 5:
            raise ValueError(f"Synonym group {group} should have 5 items, got {synonym_counts[group]}")
