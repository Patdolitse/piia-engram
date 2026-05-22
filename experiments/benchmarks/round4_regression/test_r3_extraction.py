"""R3: extract_session_insights quality regression."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from piia_engram.core import Engram

from experiments.benchmarks.round4_regression.scenarios_r3 import EXTRACTION_SCENARIOS


POSITIVE_CATEGORIES = {"lesson", "decision"}


def run_r3(
    judge: Any,
    scenarios: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scenario_list = scenarios or EXTRACTION_SCENARIOS
    rows: list[dict[str, Any]] = []

    for scenario in scenario_list:
        with tempfile.TemporaryDirectory(prefix="engram-r3-") as tmp:
            engram = Engram(root=Path(tmp) / "engram")
            extraction = engram.extract_session_insights(
                scenario["dialogue"],
                source_tool="round4_regression",
            )
        judgment = judge.judge_extraction(scenario["id"], scenario["dialogue"], extraction)
        saved_count = int(extraction.get("saved_lessons", 0)) + int(extraction.get("saved_decisions", 0))
        expected_positive = scenario["category"] in POSITIVE_CATEGORIES
        rows.append(
            {
                "id": scenario["id"],
                "category": scenario["category"],
                "dialogue": scenario["dialogue"],
                "saved_count": saved_count,
                "extraction": extraction,
                "judgment": judgment,
                "expected_positive": expected_positive,
                "recall_hit": expected_positive and bool(judgment.get("extracted_relevant")),
                "false_positive": scenario["category"] == "ordinary"
                and (bool(judgment.get("false_positive")) or saved_count > 0),
                "semantic_accuracy": float(judgment.get("semantic_accuracy", 0.0)),
            }
        )

    positive_rows = [row for row in rows if row["expected_positive"]]
    extraction_rows = [row for row in rows if row["saved_count"] > 0]
    true_positive_count = sum(1 for row in positive_rows if row["judgment"].get("extracted_relevant"))
    false_positive_count = sum(1 for row in rows if row["false_positive"])
    precision_denominator = max(1, len(extraction_rows))
    precision_numerator = max(0, len(extraction_rows) - false_positive_count)
    semantic_values = [
        row["semantic_accuracy"]
        for row in positive_rows
        if row["judgment"].get("extracted_relevant")
    ]

    summary = {
        "scenario_count": len(rows),
        "positive_count": len(positive_rows),
        "ordinary_count": len(rows) - len(positive_rows),
        "recall": true_positive_count / len(positive_rows) if positive_rows else 0.0,
        "precision": precision_numerator / precision_denominator,
        "semantic_accuracy": sum(semantic_values) / len(semantic_values) if semantic_values else 0.0,
        "false_positive_count": false_positive_count,
    }
    summary["passed"] = (
        summary["recall"] >= 0.80
        and summary["precision"] >= 0.80
        and summary["semantic_accuracy"] >= 0.85
        and summary["false_positive_count"] <= 2
    )
    return {"scenario_count": len(rows), "rows": rows, "summary": summary}

