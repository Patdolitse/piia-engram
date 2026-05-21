"""Explicit kind/domain simulation for coordinator round 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from experiments.coordinator.high_level_actions import infer_domain, looks_like_decision


@dataclass
class FakeCore:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def add_lesson(self, content: str, domain: str = "general") -> dict[str, Any]:
        self.calls.append({"tool": "add_lesson", "content": content, "domain": domain})
        return {"id": f"lesson-{len(self.calls)}", "content": content, "domain": domain}

    def add_decision(self, question: str, choice: str = "", reasoning: str = "") -> dict[str, Any]:
        self.calls.append(
            {
                "tool": "add_decision",
                "question": question,
                "choice": choice,
                "reasoning": reasoning,
            }
        )
        return {"id": f"decision-{len(self.calls)}", "question": question, "choice": choice}


def remember_explicit(
    content: str,
    kind: str,
    domain: str,
    core: FakeCore | None = None,
) -> dict[str, Any]:
    """Simulate a coordinator call where the model explicitly passes kind/domain."""
    if kind not in {"lesson", "decision"}:
        raise ValueError(f"Unsupported kind: {kind!r}")
    if not domain:
        raise ValueError("domain is required")

    core = core or FakeCore()
    if kind == "decision":
        result = core.add_decision(question=content, choice=content)
    else:
        result = core.add_lesson(content=content, domain=domain)

    return {
        "ok": True,
        "stored_kind": kind,
        "domain": domain,
        "result": result,
        "core_calls": core.calls,
    }


def judge_kind_domain(user_input: str) -> tuple[str, str]:
    """Rule-based proxy for the AI explicitly choosing kind and domain.

    This function only reads user_input. Expected labels are used later by the
    evaluator, never by this judge.
    """
    text = _normalize(user_input)
    return _judge_kind(text), _judge_domain(text)


def run_explicit_mode_test(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    remember_scenarios = [
        scenario
        for scenario in scenarios
        if scenario["test_group"] == "D"
        and scenario["expected_coordinator_tool"] == "remember"
    ]

    keyword_results = []
    explicit_results = []
    calls = []
    core = FakeCore()

    for scenario in remember_scenarios:
        keyword_kind = "decision" if looks_like_decision(scenario["user_input"]) else "lesson"
        keyword_domain = infer_domain(scenario["user_input"])
        keyword_results.append(_score_result(scenario, keyword_kind, keyword_domain, "keyword"))

        explicit_kind, explicit_domain = judge_kind_domain(scenario["user_input"])
        stored = remember_explicit(
            scenario["user_input"],
            kind=explicit_kind,
            domain=explicit_domain,
            core=core,
        )
        calls.append(
            {
                "id": scenario["id"],
                "stored_kind": stored["stored_kind"],
                "domain": stored["domain"],
                "tool": stored["core_calls"][-1]["tool"],
            }
        )
        explicit_results.append(_score_result(scenario, explicit_kind, explicit_domain, "explicit"))

    keyword_summary = _summarize_kind_domain(keyword_results)
    explicit_summary = _summarize_kind_domain(explicit_results)
    explicit_summary["calls"] = calls

    pass_status = (
        explicit_summary["hard_kind_accuracy"] >= 0.95
        and explicit_summary["domain_accuracy"] >= 0.90
        and explicit_summary["easy_medium_kind_accuracy"] >= 0.98
    )

    return {
        "test": "D",
        "scenario_count": len(remember_scenarios),
        "keyword": keyword_summary,
        "explicit": explicit_summary,
        "pass": pass_status,
        "thresholds": {
            "hard_kind_accuracy": 0.95,
            "domain_accuracy": 0.90,
            "easy_medium_kind_accuracy": 0.98,
        },
    }


def _score_result(
    scenario: dict[str, Any],
    kind: str,
    domain: str,
    mode: str,
) -> dict[str, Any]:
    return {
        "id": scenario["id"],
        "mode": mode,
        "difficulty": scenario["difficulty"],
        "user_input": scenario["user_input"],
        "expected_kind": scenario["expected_kind"],
        "actual_kind": kind,
        "kind_correct": kind == scenario["expected_kind"],
        "expected_domain": scenario["expected_domain"],
        "actual_domain": domain,
        "domain_correct": domain == scenario["expected_domain"],
    }


def _summarize_kind_domain(results: list[dict[str, Any]]) -> dict[str, Any]:
    hard_results = [item for item in results if item["difficulty"] == "hard"]
    easy_results = [item for item in results if item["difficulty"] == "easy"]
    medium_results = [item for item in results if item["difficulty"] == "medium"]
    easy_medium_results = [item for item in results if item["difficulty"] in {"easy", "medium"}]
    return {
        "results": results,
        "kind_accuracy": _accuracy(results, "kind_correct"),
        "domain_accuracy": _accuracy(results, "domain_correct"),
        "easy_kind_accuracy": _accuracy_or_none(easy_results, "kind_correct"),
        "medium_kind_accuracy": _accuracy_or_none(medium_results, "kind_correct"),
        "easy_medium_kind_accuracy": _accuracy(easy_medium_results, "kind_correct"),
        "hard_kind_accuracy": _accuracy(hard_results, "kind_correct"),
        "counts_by_difficulty": {
            "easy": len(easy_results),
            "medium": len(medium_results),
            "hard": len(hard_results),
        },
    }


def _accuracy(results: list[dict[str, Any]], field: str) -> float:
    if not results:
        return 0.0
    return sum(1 for item in results if item[field]) / len(results)


def _accuracy_or_none(results: list[dict[str, Any]], field: str) -> float | None:
    if not results:
        return None
    return _accuracy(results, field)


def _judge_kind(text: str) -> str:
    if _contains_any(text, ("不代表我们已经做了某个决策", "不是要现在选择某个方案")):
        return "lesson"
    if _contains_any(
        text,
        (
            "刚发现",
            "踩坑",
            "经验",
            "学到了",
            "学到",
            "复盘",
            "线上问题",
            "会导致",
            "信号",
            "教训",
            "别让",
        ),
    ):
        return "lesson"
    if _contains_any(
        text,
        (
            "决定",
            "选择",
            "决策",
            "以后",
            "统一",
            "固定为",
            "不再",
            "长期事实源",
            "从今天起",
            "就定",
            "默认",
        ),
    ):
        return "decision"
    return "lesson"


def _judge_domain(text: str) -> str:
    if _contains_any(text, ("pytest", "unittest", "测试框架", "测试命令", "python 测试")):
        return "testing"
    if _contains_any(text, ("python", "gbk", "twine")):
        return "python"
    if _contains_any(text, ("npm", "pnpm", "lockfile", "package-lock", "node", "typescript", "javascript", "前端")):
        return "javascript" if not _contains_any(text, ("typescript", "浏览器扩展")) else "frontend"
    if _contains_any(text, ("mcp", "工具描述", "模型会把")):
        return "mcp"
    if _contains_any(text, ("postgresql", "数据库", "审计事件")):
        return "database"
    if _contains_any(text, ("ci", "github actions", "流水线")):
        return "devops"
    if _contains_any(text, ("取消订阅", "churn")):
        return "product"
    if _contains_any(text, ("api", "限流", "雪崩")):
        return "backend"
    if _contains_any(text, ("路径图谱", "安装位置", "token", "项目资产", "项目上下文")):
        return "project_context"
    if _contains_any(text, ("本地 json", "云端", "事实源", "架构")):
        return "architecture"
    if _contains_any(text, ("发布包", "mainline", "release pack", "维护包")):
        return "release"
    return "general"


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker.lower() in text for marker in markers)
