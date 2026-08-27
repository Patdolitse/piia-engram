#!/usr/bin/env python3
"""Attack-corpus evaluator for the session-end content digest (v1).

Loads the symbolic corpus (tests/fixtures/hook_digest_attack_corpus_v1.json),
expands placeholders deterministically, and evaluates each case against the
digest/guard pipeline. Outputs a metadata-only summary: case IDs and
pass/fail, never expanded secret content (receipt discipline).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for path in (ROOT, SRC):
    p = str(path)
    if p not in sys.path:
        sys.path.insert(0, p)

CORPUS_PATH = ROOT / "tests" / "fixtures" / "hook_digest_attack_corpus_v1.json"


def load_corpus(path: Path = CORPUS_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "engram.hook_digest_attack_corpus.v1":
        raise ValueError("corpus schema mismatch")
    return data


def _expand(text: str, symbols: dict) -> str:
    for name, value in symbols.items():
        # credential-shaped symbols are stored as fragment arrays so the
        # tracked corpus never holds a literal the sanitizer must flag
        expanded = "".join(value) if isinstance(value, list) else value
        text = text.replace("${" + name + "}", expanded)
    return text


def expanded_cases(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    symbols = corpus.get("symbols", {})
    cases = []
    for case in corpus.get("cases", []):
        expanded = json.loads(json.dumps(case))  # deep copy
        # expand transcript lines (serialize->expand->parse keeps JSON shape)
        raw = json.dumps(expanded.get("transcript_lines", []))
        expanded["transcript_lines"] = json.loads(_expand(raw, symbols))
        expanded["required"] = [_expand(x, symbols) for x in expanded.get("required", [])]
        expanded["forbidden"] = [_expand(x, symbols) for x in expanded.get("forbidden", [])]
        cases.append(expanded)
    return cases


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one case; returns {id, layer, oracle, passed, problems}.

    Problems are STABLE CODES only — never the expanded secret text.
    """
    from piia_engram.hook_digest import build_digest, output_guard_item

    problems: list[str] = []
    lines = [json.dumps(entry) for entry in case["transcript_lines"]]
    digest = build_digest(lines)

    oracle = case["oracle"]
    if oracle == "drop":
        if digest is not None:
            for forbidden in case.get("forbidden", []):
                if forbidden and forbidden in digest:
                    problems.append("forbidden_substance_in_digest")
                    break
            else:
                problems.append("expected_drop_got_digest")
    elif oracle == "preserve":
        if digest is None:
            problems.append("expected_digest_got_none")
        else:
            for required in case.get("required", []):
                if required not in digest:
                    problems.append("required_missing_in_digest")
                    break
            for forbidden in case.get("forbidden", []):
                if forbidden and forbidden in digest:
                    problems.append("forbidden_substance_in_digest")
                    break

    if case.get("layer") == "guard_window" and digest is not None:
        # simulate the extraction window guard over the digest
        ok, _reason = output_guard_item({"sentence": digest})
        if not ok and oracle == "preserve":
            problems.append("guard_rejected_clean_digest")

    return {
        "id": case["id"],
        "layer": case.get("layer", ""),
        "oracle": oracle,
        "passed": not problems,
        "problems": problems,
    }


def run_eval() -> dict[str, Any]:
    corpus = load_corpus()
    results = [evaluate_case(c) for c in expanded_cases(corpus)]
    passed = sum(1 for r in results if r["passed"])
    return {
        "schema": "engram.hook_digest_attack_eval.v1",
        "case_count": len(results),
        "passed_count": passed,
        "failed_count": len(results) - passed,
        "overall_passed": passed == len(results),
        "cases": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    summary = run_eval()
    if args.as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Overall: {'PASS' if summary['overall_passed'] else 'FAIL'} "
              f"({summary['passed_count']}/{summary['case_count']})")
        for r in summary["cases"]:
            if not r["passed"]:
                print(f"  FAIL {r['id']} [{r['layer']}/{r['oracle']}]: {','.join(r['problems'])}")
    return 0 if summary["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
