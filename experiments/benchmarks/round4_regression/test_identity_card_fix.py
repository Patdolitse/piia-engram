"""Verify that export_identity_card includes decisions after the R2 fix."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from engram_core.core import Engram

from experiments.benchmarks.round4_regression.llm_judge import DeepSeekClient, _parse_json_response


ROUND4_DIR = Path(__file__).resolve().parent
TITLE = "## 我的关键决策（请遵循）"


def seed_identity_card_engram(engram: Engram) -> dict[str, Any]:
    """Seed a temporary Engram with enough lessons and decisions for card export."""
    engram.update_profile(
        {
            "role": "Engram 回归验证者",
            "language": "中文",
            "tech_stack": "Python, pytest, DeepSeek",
            "description": "负责确认冷启动上下文和身份卡不会遗漏关键决策。",
        }
    )
    lessons = [
        "验证修复必须使用临时 Engram 根目录，不能污染真实用户数据。",
        "LLM judge 的正例必须有可复制的证据片段。",
        "报告里要区分修复前基线和修复后结果。",
    ]
    decisions = [
        {
            "question": "身份卡是否应包含关键决策？",
            "choice": "必须包含最近的关键决策，避免 AI 重复讨论已定事项",
            "reasoning": "身份卡用于跨工具冷启动，decision 与 lesson 同样影响后续行为。",
            "domain": "identity",
        },
        {
            "question": "验证 R2 修复时是否修改主代码？",
            "choice": "不修改主代码，只运行验证并报告结果",
            "reasoning": "任务边界是验证已完成修复，而不是继续实现。",
            "domain": "testing",
        },
        {
            "question": "身份卡验证是否需要语义判定？",
            "choice": "需要 DeepSeek 判断输出是否包含决策信息",
            "reasoning": "静态字符串命中只能证明文本出现，语义判断补充用户视角。",
            "domain": "testing",
        },
    ]
    for lesson in lessons:
        engram.add_lesson(lesson, domain="testing", source_tool="round4_identity_card_fix")
    for decision in decisions:
        engram.add_decision(decision, source_tool="round4_identity_card_fix")
    return {"lessons": lessons, "decisions": decisions}


def run_identity_card_fix_check(
    output_dir: str | Path | None = None,
    client: Any | None = None,
    runs_per_scenario: int = 3,
) -> dict[str, Any]:
    """Run the identity-card fix verification and optionally persist artifacts."""
    with tempfile.TemporaryDirectory(prefix="engram-card-fix-") as tmp:
        engram = Engram(root=Path(tmp) / "engram")
        seed = seed_identity_card_engram(engram)
        card = engram.export_identity_card()

    static_title_present = TITLE in card
    static_decision_text_present = any(
        decision["question"] in card or decision["choice"] in card
        for decision in seed["decisions"]
    )
    visible_decision_count = sum(
        1
        for decision in seed["decisions"]
        if decision["question"] in card or decision["choice"] in card
    )

    target_dir = Path(output_dir) if output_dir else None
    raw_log_path = target_dir / "results_identity_card_fix_raw.jsonl" if target_dir else None
    if raw_log_path and raw_log_path.exists():
        raw_log_path.unlink()

    llm_result = judge_identity_card(card, client=client, raw_log_path=raw_log_path, runs_per_scenario=runs_per_scenario)
    result = {
        "static_title_present": static_title_present,
        "static_decision_text_present": static_decision_text_present,
        "visible_decision_count": visible_decision_count,
        "llm": llm_result,
        "passed": static_title_present
        and static_decision_text_present
        and visible_decision_count >= 1
        and llm_result["passed"],
    }
    if target_dir:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "results_identity_card_fix.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return result


def judge_identity_card(
    card: str,
    client: Any | None = None,
    raw_log_path: Path | None = None,
    runs_per_scenario: int = 3,
) -> dict[str, Any]:
    """Ask DeepSeek whether the exported identity card contains decision information."""
    judge_client = client or DeepSeekClient()
    parsed_results = []
    for run_index in range(1, runs_per_scenario + 1):
        messages = [{"role": "user", "content": build_identity_card_prompt(card)}]
        response = judge_client.complete(messages, max_tokens=450)
        parsed = _normalise_identity_card_judgment(_parse_json_response(response.get("content", "")), card)
        parsed_results.append(parsed)
        if raw_log_path:
            raw_log_path.parent.mkdir(parents=True, exist_ok=True)
            with raw_log_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "scenario_id": "identity_card_decisions",
                            "mode": "identity_card",
                            "run_index": run_index,
                            "messages": messages,
                            "response": response,
                            "raw_content": response.get("content", ""),
                            "parsed": parsed,
                            "usage": response.get("usage", {}),
                            "error": None,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    keys = [
        json.dumps(
            {
                "has_key_decisions_section": item["has_key_decisions_section"],
                "has_concrete_decision": item["has_concrete_decision"],
            },
            sort_keys=True,
        )
        for item in parsed_results
    ]
    winner_key = Counter(keys).most_common(1)[0][0]
    winner = dict(parsed_results[keys.index(winner_key)])
    winner["positive_runs"] = sum(
        1
        for item in parsed_results
        if item["has_key_decisions_section"] and item["has_concrete_decision"]
    )
    winner["runs_per_scenario"] = runs_per_scenario
    winner["passed"] = winner["positive_runs"] >= 2
    return winner


def build_identity_card_prompt(card: str) -> str:
    return (
        "You are verifying an exported AI identity card.\n"
        "Decide whether the card contains a dedicated key-decisions section and at least one concrete decision.\n"
        "A concrete decision should include a question, choice, or settled direction, not merely a general preference.\n"
        "Use only the card text as evidence. If you answer true, evidence_quote must be copied exactly from the card.\n"
        "Return JSON only.\n\n"
        f"Identity card:\n{card}\n\n"
        "Return this exact JSON object:\n"
        '{"has_key_decisions_section": true, "has_concrete_decision": true, '
        '"visible_decision_count": 1, "evidence_quote": "...", "reasoning": "..."}'
    )


def test_export_identity_card_includes_decisions_with_deepseek(tmp_path):
    result = run_identity_card_fix_check(output_dir=tmp_path, runs_per_scenario=3)
    assert result["static_title_present"]
    assert result["static_decision_text_present"]
    assert result["visible_decision_count"] >= 1
    assert result["llm"]["positive_runs"] >= 2
    assert result["passed"]


def _normalise_identity_card_judgment(data: dict[str, Any], card: str) -> dict[str, Any]:
    evidence = str(data.get("evidence_quote") or "").strip().strip('"').strip("'")
    evidence_present = bool(evidence) and evidence in card
    return {
        "has_key_decisions_section": _as_bool(data.get("has_key_decisions_section")) and TITLE in card,
        "has_concrete_decision": _as_bool(data.get("has_concrete_decision")) and evidence_present,
        "visible_decision_count": _as_int(data.get("visible_decision_count")),
        "evidence_quote": evidence if evidence_present else "",
        "reasoning": str(data.get("reasoning", "")),
    }


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "y"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


if __name__ == "__main__":
    print(json.dumps(run_identity_card_fix_check(output_dir=ROUND4_DIR), ensure_ascii=False, indent=2))
