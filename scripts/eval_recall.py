"""Offline recall relevance evaluation harness.

Runs real Engram search over tiny synthetic fixtures and scores by expected
knowledge IDs. This deliberately avoids live stores, network calls, and fuzzy
LLM judges so the result is deterministic and debuggable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from piia_engram import Engram  # noqa: E402
from piia_engram import version_chain as _version_chain  # noqa: E402
from piia_engram.governance_store import RelationStore  # noqa: E402


DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "recall_eval_v1.json"
DEFAULT_THRESHOLDS = {
    "min_mean_recall_at_k": 0.8,
    "min_mean_mrr": 0.7,
    "max_forbidden_leak_rate": 0.0,
    "max_negative_false_positive_rate": 0.1,
}


def load_corpus(path: str | Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    """Load and minimally validate the recall eval fixture."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("recall eval fixture schema must be 1")
    if not isinstance(data.get("stores"), dict) or not data["stores"]:
        raise ValueError("recall eval fixture requires stores")
    if not isinstance(data.get("cases"), list) or not data["cases"]:
        raise ValueError("recall eval fixture requires cases")
    thresholds = dict(DEFAULT_THRESHOLDS)
    thresholds.update(data.get("thresholds") or {})
    data["thresholds"] = thresholds
    return data


def _materialize_project_folders(
    store: dict[str, Any], case: dict[str, Any], case_root: Path
) -> dict[str, str]:
    """Map fixture folder strings to real per-case directories on disk.

    Fixtures carry platform-shaped path strings (e.g. ``E:/synthetic/alpha``)
    that never exist. Resolving those against the process cwd is
    OS-dependent, so every fixture folder is materialized as a real directory
    under the case root and both seeding and queries use the real path. This
    keeps project identity well-defined on every platform.
    """
    folder_strings: list[str] = []
    for meta in (store.get("projects") or {}).values():
        folder = str((meta or {}).get("folder") or "")
        if folder and folder not in folder_strings:
            folder_strings.append(folder)
    case_folder = str(case.get("project_folder") or "")
    if case_folder and case_folder not in folder_strings:
        folder_strings.append(case_folder)
    base = case_root / "projects"
    mapping: dict[str, str] = {}
    for index, folder in enumerate(folder_strings):
        real = base / f"p{index}"
        real.mkdir(parents=True, exist_ok=True)
        mapping[folder] = str(real)
    return mapping


def _seed_engram(
    store: dict[str, Any], root: Path, folder_map: dict[str, str] | None = None
) -> Engram:
    """Create an isolated Engram store from one synthetic store fixture."""
    previous_test_flag = os.environ.get("ENGRAM_TEST")
    os.environ["ENGRAM_TEST"] = "1"
    try:
        eng = Engram(root=root)
    finally:
        if previous_test_flag is None:
            os.environ.pop("ENGRAM_TEST", None)
        else:
            os.environ["ENGRAM_TEST"] = previous_test_flag
    projects = store.get("projects") or {}
    for lesson in store.get("lessons") or []:
        eng.add_lesson(dict(lesson))
    for decision in store.get("decisions") or []:
        eng.add_decision(dict(decision))
    for playbook in store.get("playbooks") or []:
        item = dict(playbook)
        # Fixture project IDs are human-readable labels. Engram computes its
        # canonical project id from the folder path, so convert labels to
        # project_folder and let add_playbook normalize the real id.
        fixture_project_id = item.get("project_id")
        if fixture_project_id and projects.get(fixture_project_id):
            item["project_folder"] = folder_map.get(
                projects[fixture_project_id]["folder"],
                projects[fixture_project_id]["folder"],
            )
            item.pop("project_id", None)
        eng.add_playbook(item)

    relation_store = RelationStore(root)
    for edge in store.get("relation_edges") or []:
        if not isinstance(edge, dict):
            continue
        relation_store.add_relation(
            str(edge.get("src") or ""),
            str(edge.get("rel") or ""),
            str(edge.get("dst") or ""),
        )

    # Save project snapshots after playbooks are written; visibility checks use
    # project ids derived from folder paths, and the synthetic folders never need
    # to exist on disk.
    for project_id, meta in projects.items():
        folder = str(meta.get("folder") or "")
        if not folder:
            continue
        eng.save_project_snapshot(
            folder_map.get(folder, folder),
            {
                "title": meta.get("title", project_id),
                "tech_stack": meta.get("tech_stack", []),
                "notes": "synthetic recall eval project",
            },
        )
    return eng


def _flatten_rows(search_result: dict[str, Any], relation_edges: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket in ("lessons", "decisions", "playbooks"):
        bucket_rows = search_result.get(bucket)
        if not isinstance(bucket_rows, list):
            continue
        cleaned = [row for row in bucket_rows if isinstance(row, dict)]
        if relation_edges:
            cleaned, _collapsed = _version_chain.collapse_to_heads(cleaned, relation_edges)
        for row in cleaned:
            if isinstance(row, dict) and row.get("id"):
                rows.append(row)
    return rows


def _flatten_ids(search_result: dict[str, Any], relation_edges: list[dict[str, Any]] | None = None) -> list[str]:
    return [str(row["id"]) for row in _flatten_rows(search_result, relation_edges)]


def _score_ids(actual_ids: list[str], expected_ids: list[str], forbidden_ids: list[str]) -> dict[str, Any]:
    expected = list(expected_ids)
    forbidden = list(forbidden_ids)
    actual = list(actual_ids)
    expected_set = set(expected)
    actual_set = set(actual)
    forbidden_leak = any(item_id in actual_set for item_id in forbidden)

    if not expected:
        false_positive = bool(actual)
        return {
            "hit_at_k": not false_positive,
            "precision_at_k": 1.0 if not false_positive else 0.0,
            "recall_at_k": 1.0 if not false_positive else 0.0,
            "mrr": 1.0 if not false_positive else 0.0,
            "forbidden_leak": forbidden_leak,
            "false_positive": false_positive,
        }

    hits = [item_id for item_id in actual if item_id in expected_set]
    precision = len(hits) / len(actual) if actual else 0.0
    recall = len(set(hits)) / len(expected)
    first_rank = next((idx + 1 for idx, item_id in enumerate(actual) if item_id in expected_set), None)
    return {
        "hit_at_k": bool(hits),
        "precision_at_k": precision,
        "recall_at_k": recall,
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "forbidden_leak": forbidden_leak,
        "false_positive": False,
    }


def evaluate_case(corpus: dict[str, Any], case: dict[str, Any], workdir: str | Path) -> dict[str, Any]:
    """Evaluate one labeled query against its synthetic store.

    v4.20: a case may set ``"surface": "get_recall"`` to run through the REAL
    recall surface (``gather_recall`` with ``include_playbooks`` honored from
    the case) instead of raw ``search_knowledge`` — the harness previously
    never exercised the projection/budget path, so playbook-recall cases can
    only be proven here. ``no_steps_leak: true`` asserts the projected payload
    contains no playbook ``steps`` bytes at all.
    """
    store_id = str(case.get("store") or "")
    stores = corpus.get("stores") or {}
    if store_id not in stores:
        raise ValueError(f"unknown recall eval store: {store_id}")

    case_root = Path(workdir) / "stores" / str(case["id"])
    folder_map = _materialize_project_folders(stores[store_id], case, case_root)
    eng = _seed_engram(stores[store_id], case_root, folder_map)
    surface = str(case.get("surface") or "search")
    if surface == "get_recall":
        from piia_engram.recall_service import gather_recall

        payload = gather_recall(
            eng,
            project_folder=folder_map.get(
                str(case.get("project_folder") or ""), str(case.get("project_folder") or "")
            ) or "",
            query=str(case.get("query") or ""),
            limit=int(case.get("limit") or 5),
            include_playbooks=bool(case.get("include_playbooks")),
        )
        actual_ids = [
            str(item["id"])
            for item in payload.get("knowledge") or []
            if isinstance(item, dict) and item.get("id")
        ]
        if case.get("no_steps_leak"):
            import json as _json

            assert "steps" not in _json.dumps(payload, ensure_ascii=False), (
                f"case {case['id']}: playbook steps leaked into the recall payload")
    else:
        result = eng.search_knowledge(
            str(case.get("query") or ""),
            scope=str(case.get("scope") or "all"),
            limit=int(case.get("limit") or 5),
            filters=case.get("filters") or None,
            allow_hybrid_index=False,
            project_folder=folder_map.get(str(case.get("project_folder") or ""),
                                           str(case.get("project_folder") or "")) or None,
        )
        relation_edges = stores[store_id].get("relation_edges") or []
        actual_ids = _flatten_ids(result, relation_edges)
    expected_ids = [str(item_id) for item_id in case.get("expected_ids") or []]
    forbidden_ids = [str(item_id) for item_id in case.get("forbidden_ids") or []]
    scored = _score_ids(actual_ids, expected_ids, forbidden_ids)
    passed = (
        scored["hit_at_k"]
        and scored["forbidden_leak"] is False
        and scored["false_positive"] is False
        and (not expected_ids or bool(actual_ids and actual_ids[0] in set(expected_ids)))
    )
    return {
        "case_id": str(case["id"]),
        "scenario": str(case.get("scenario") or ""),
        "query": str(case.get("query") or ""),
        "surface": surface,
        "expected_ids": expected_ids,
        "forbidden_ids": forbidden_ids,
        "actual_ids": actual_ids,
        **scored,
        "passed": passed,
    }


def evaluate_corpus(corpus: dict[str, Any], workdir: str | Path) -> dict[str, Any]:
    """Evaluate all cases and aggregate metrics."""
    cases = [evaluate_case(corpus, case, workdir) for case in corpus["cases"]]
    count = len(cases) or 1
    negative_cases = [case for case in cases if not case["expected_ids"]]
    negative_count = len(negative_cases) or 1
    metrics = {
        "mean_precision_at_k": sum(float(case["precision_at_k"]) for case in cases) / count,
        "mean_recall_at_k": sum(float(case["recall_at_k"]) for case in cases) / count,
        "mean_mrr": sum(float(case["mrr"]) for case in cases) / count,
        "forbidden_leak_rate": sum(1 for case in cases if case["forbidden_leak"]) / count,
        "negative_false_positive_rate": (
            sum(1 for case in negative_cases if case["false_positive"]) / negative_count
        ),
    }
    thresholds = dict(corpus.get("thresholds") or DEFAULT_THRESHOLDS)
    threshold_passed = (
        metrics["mean_recall_at_k"] >= float(thresholds["min_mean_recall_at_k"])
        and metrics["mean_mrr"] >= float(thresholds["min_mean_mrr"])
        and metrics["forbidden_leak_rate"] <= float(thresholds["max_forbidden_leak_rate"])
        and metrics["negative_false_positive_rate"] <= float(thresholds["max_negative_false_positive_rate"])
    )
    passed_count = sum(1 for case in cases if case["passed"])
    return {
        "schema": 1,
        "benchmark": str(corpus.get("benchmark") or "recall_eval_v1"),
        "public_safe": bool(corpus.get("public_safe")),
        "case_count": len(cases),
        "passed_count": passed_count,
        "failed_count": len(cases) - passed_count,
        "overall_passed": passed_count == len(cases) and threshold_passed,
        "metrics": metrics,
        "thresholds": thresholds,
        "cases": cases,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    """Render a compact, shareable recall eval report."""
    metrics = summary.get("metrics") or {}
    lines = [
        "# Recall Eval v1",
        "",
        f"Overall: {'PASS' if summary.get('overall_passed') else 'FAIL'}",
        f"Cases: {summary.get('passed_count')}/{summary.get('case_count')} passed",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| mean precision@k | {metrics.get('mean_precision_at_k', 0):.3f} |",
        f"| mean recall@k | {metrics.get('mean_recall_at_k', 0):.3f} |",
        f"| mean MRR | {metrics.get('mean_mrr', 0):.3f} |",
        f"| forbidden leak rate | {metrics.get('forbidden_leak_rate', 0):.3f} |",
        f"| negative false-positive rate | {metrics.get('negative_false_positive_rate', 0):.3f} |",
        "",
        "| Case | Scenario | Pass | Expected | Actual |",
        "|---|---|---:|---|---|",
    ]
    for case in summary.get("cases") or []:
        expected = ", ".join(case.get("expected_ids") or []) or "(none)"
        actual = ", ".join(case.get("actual_ids") or []) or "(none)"
        lines.append(
            f"| {case.get('case_id')} | {case.get('scenario')} | "
            f"{'yes' if case.get('passed') else 'no'} | {expected} | {actual} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    parser.add_argument("--workdir", default="")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    corpus = load_corpus(args.fixture)
    if args.workdir:
        summary = evaluate_corpus(corpus, Path(args.workdir))
    else:
        with tempfile.TemporaryDirectory(prefix="engram-recall-eval-") as workdir:
            summary = evaluate_corpus(corpus, Path(workdir))
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(summary), end="")
    return 0 if summary["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
