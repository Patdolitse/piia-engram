"""T1: direct checks for decision domain write/filter behavior."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from piia_engram.core import Engram


def run_t1() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="engram-round8-decisions-") as tmp:
        engram = Engram(Path(tmp))

        single = engram.add_decision(
            "round8 alpha choose modular architecture boundary",
            "Adopt layered modules for service boundaries",
            "Single architecture-domain write case",
            domain="architecture",
            source_tool="round8_test",
        )
        multi = engram.add_decision(
            "round8 bravo store audit events in relational database",
            "Persist audit events in PostgreSQL tables",
            "Multi-domain database plus architecture write case",
            domain="architecture,database",
            source_tool="round8_test",
        )
        legacy = engram.add_decision(
            "round8 charlie keep old import format readable",
            "Preserve legacy import compatibility",
            "Backward compatibility case",
            source_tool="round8_test",
        )

        write_results = _run_write_cases(single, multi)
        filter_results = _run_filter_cases(engram, single, multi, legacy)
        compatibility_results = _run_compatibility_cases(engram, single, multi, legacy)

    groups = {
        "write": write_results,
        "filter": filter_results,
        "compatibility": compatibility_results,
    }
    all_rows = [row for rows in groups.values() for row in rows]
    return {
        "test": "T1",
        "scenario_count": len(all_rows),
        "correct": sum(1 for row in all_rows if row["correct"]),
        "passed": all(row["correct"] for row in all_rows),
        "groups": {
            name: {
                "total": len(rows),
                "correct": sum(1 for row in rows if row["correct"]),
                "passed": all(row["correct"] for row in rows),
                "results": rows,
            }
            for name, rows in groups.items()
        },
        "results": all_rows,
    }


def _run_write_cases(single: dict[str, Any], multi: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "T1-WRITE-01",
            "category": "write",
            "input": "add_decision(domain='architecture')",
            "expected": "architecture",
            "actual": single.get("domain"),
            "correct": single.get("domain") == "architecture",
        },
        {
            "id": "T1-WRITE-02",
            "category": "write",
            "input": "add_decision(domain='architecture,database')",
            "expected": "architecture,database",
            "actual": multi.get("domain"),
            "correct": multi.get("domain") == "architecture,database",
        },
    ]


def _run_filter_cases(
    engram: Engram,
    single: dict[str, Any],
    multi: dict[str, Any],
    legacy: dict[str, Any],
) -> list[dict[str, Any]]:
    architecture = engram.get_decisions(domain="architecture", limit=None)
    database = engram.get_decisions(domain="database", limit=None)
    python = engram.get_decisions(domain="python", limit=None)
    all_decisions = engram.get_decisions(domain=None, limit=None)

    return [
        {
            "id": "T1-FILTER-01",
            "category": "filter",
            "input": "domain=architecture",
            "expected": [single["id"], multi["id"]],
            "actual": _ids(architecture),
            "correct": _contains_exact(architecture, {single["id"], multi["id"]}),
        },
        {
            "id": "T1-FILTER-02",
            "category": "filter",
            "input": "domain=database",
            "expected": [multi["id"]],
            "actual": _ids(database),
            "correct": _contains_exact(database, {multi["id"]}),
        },
        {
            "id": "T1-FILTER-03",
            "category": "filter",
            "input": "domain=python",
            "expected": [],
            "actual": _ids(python),
            "correct": not python,
        },
        {
            "id": "T1-FILTER-04",
            "category": "filter",
            "input": "domain=None",
            "expected": [single["id"], multi["id"], legacy["id"]],
            "actual": _ids(all_decisions),
            "correct": _contains_exact(all_decisions, {single["id"], multi["id"], legacy["id"]}),
        },
    ]


def _run_compatibility_cases(
    engram: Engram,
    single: dict[str, Any],
    multi: dict[str, Any],
    legacy: dict[str, Any],
) -> list[dict[str, Any]]:
    all_decisions = engram.get_decisions(domain=None, limit=None)
    architecture = engram.get_decisions(domain="architecture", limit=None)
    return [
        {
            "id": "T1-COMPAT-01",
            "category": "compatibility",
            "input": "unfiltered get_decisions includes no-domain decision",
            "expected": True,
            "actual": legacy["id"] in _ids(all_decisions),
            "correct": legacy["id"] in _ids(all_decisions)
            and single["id"] in _ids(all_decisions)
            and multi["id"] in _ids(all_decisions),
        },
        {
            "id": "T1-COMPAT-02",
            "category": "compatibility",
            "input": "domain=architecture excludes no-domain decision",
            "expected": False,
            "actual": legacy["id"] in _ids(architecture),
            "correct": legacy["id"] not in _ids(architecture),
        },
    ]


def _ids(items: list[dict[str, Any]]) -> list[str]:
    return [item["id"] for item in items]


def _contains_exact(items: list[dict[str, Any]], expected_ids: set[str]) -> bool:
    return set(_ids(items)) == expected_ids


def test_t1_decisions_domain() -> None:
    result = run_t1()
    assert result["groups"]["write"]["correct"] == 2
    assert result["groups"]["filter"]["correct"] == 4
    assert result["groups"]["compatibility"]["correct"] == 2
