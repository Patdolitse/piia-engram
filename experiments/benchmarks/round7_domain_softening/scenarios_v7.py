"""Round 7 regression scenarios sampled from Round 6."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from experiments.benchmarks.round6_full_coverage.scenarios_v6 import SCENARIOS_V6


ROUND6_BY_ID = {scenario["id"]: scenario for scenario in SCENARIOS_V6}


def _round7_copy(scenario_id: str) -> dict[str, Any]:
    source = deepcopy(ROUND6_BY_ID[scenario_id])
    source["round6_id"] = source["id"]
    source["id"] = f"R7-{source['id']}"
    source["test_group"] = "T2"
    return source


T2_SCENARIO_IDS = [
    # G1: identity + project-context surface
    "G1-USER-CONTEXT-01",
    "G1-IDENTITY-CARD-01",
    "G1-PROJECT-CONTEXT-01",
    "G1-SAVE-SNAPSHOT-01",
    "G1-DOMAINS-01",
    # G2: recording/retrieval, including the known Round 6 get_decisions ambiguity
    "G2-ADD-LESSON-01",
    "G2-ADD-DECISION-01",
    "G2-SEARCH-01",
    "G2-RELEVANT-01",
    "G2-GET-DECISIONS-01",
    # G3: maintenance + workflow shortcuts
    "G3-EXPORT-ENGRAM-01",
    "G3-EXPORT-OPENCLAW-01",
    "G3-AUDIT-01",
    "G3-WRAP-SESSION-01",
    "G3-START-PROJECT-01",
    # G4: no-tool + missing-parameter boundaries
    "G4-NONE-01",
    "G4-NONE-02",
    "G4-MISSING-01",
    "G4-MISSING-02",
    "G4-MISSING-05",
]


T2_SCENARIOS: list[dict[str, Any]] = [
    _round7_copy(scenario_id) for scenario_id in T2_SCENARIO_IDS
]


def validate_scenarios_v7(scenarios: list[dict[str, Any]] = T2_SCENARIOS) -> None:
    if len(scenarios) != 20:
        raise ValueError(f"Expected 20 T2 scenarios, got {len(scenarios)}")
    ids = [scenario["id"] for scenario in scenarios]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate Round 7 scenario IDs")
    group_counts: dict[str, int] = {}
    for scenario in scenarios:
        missing = {"id", "round6_id", "user_input", "expected_tool", "category"} - set(scenario)
        if missing:
            raise ValueError(f"{scenario.get('id', '<unknown>')} missing {sorted(missing)}")
        prefix = scenario["round6_id"].split("-", 1)[0]
        group_counts[prefix] = group_counts.get(prefix, 0) + 1
    if group_counts != {"G1": 5, "G2": 5, "G3": 5, "G4": 5}:
        raise ValueError(f"Unexpected source group counts: {group_counts}")
