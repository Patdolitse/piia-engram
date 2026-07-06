"""MCIC scenario definitions and client A/B method contract.

Public-safe, deterministic contract layer for cross-client identity
continuity (MCIC) evaluation.  All scenario data is synthetic.
No model calls, no network access, no private user data.

API
---
default_mcic_scenarios()   -> list of scenario dicts
validate_scenario(s)       -> list of issues (empty == valid)
validate_ab_run(run)       -> {valid, blocking, warnings}
score_ab_result(run)       -> separate sub-scores (never a single magic number)
render_public_summary(...) -> public-safe text (metadata/synthetic only)
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MCIC_CATEGORIES = frozenset({
    "explicit_recall",
    "implicit_personalization",
    "adversarial_false_premise",
    "negative_control",
    "latency",
})

SCENARIO_REQUIRED_FIELDS = frozenset({
    "id",
    "category",
    "client_a_action",
    "client_b_prompt",
    "expected_signal",
    "failure_mode",
})

AB_RUN_REQUIRED_FIELDS = frozenset({
    "scenario_id",
    "arm",
    "session_dir",
    "home_dir",
    "response",
    "latency_ms",
})

_PRIVATE_MARKERS = re.compile(
    r"(?:"
    r"sk-proj-[A-Za-z0-9]+"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|[A-Z]:\\Users\\[^\\\/\s]+"
    r"|/home/[a-z_][a-z0-9_-]*"
    r"|/Users/[A-Za-z][A-Za-z0-9._-]*"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r")",
)

DEFAULT_LATENCY_BUDGET_MS = 2000.0

# ---------------------------------------------------------------------------
# Scenario definitions (synthetic, public-safe)
# ---------------------------------------------------------------------------


def default_mcic_scenarios() -> list[dict[str, Any]]:
    """Return the canonical MCIC scenario set.

    Every scenario uses synthetic data only.  At least 10 scenarios,
    covering all five MCIC_CATEGORIES.
    """
    return [
        # -- explicit_recall (4) ------------------------------------
        {
            "id": "ER-01",
            "category": "explicit_recall",
            "client_a_action": (
                "Store preference: preferred_language=Rust "
                "via add_lesson"
            ),
            "client_b_prompt": (
                "What programming language do I prefer for new projects?"
            ),
            "expected_signal": (
                "Response mentions Rust as the preferred language"
            ),
            "failure_mode": (
                "Language preference not recalled or wrong language stated"
            ),
        },
        {
            "id": "ER-02",
            "category": "explicit_recall",
            "client_a_action": (
                "Store fact: timezone=UTC+8 via update_preferences"
            ),
            "client_b_prompt": (
                "What timezone should you schedule meetings in for me?"
            ),
            "expected_signal": (
                "Response references UTC+8 or equivalent offset"
            ),
            "failure_mode": (
                "Timezone not recalled or defaults to another zone"
            ),
        },
        {
            "id": "ER-03",
            "category": "explicit_recall",
            "client_a_action": (
                "Store decision: chose PostgreSQL over MySQL "
                "for the analytics service via add_decision"
            ),
            "client_b_prompt": (
                "Which database did we pick for the analytics service?"
            ),
            "expected_signal": (
                "Response names PostgreSQL and references the decision"
            ),
            "failure_mode": (
                "Database choice not recalled or conflated with another project"
            ),
        },
        {
            "id": "ER-04",
            "category": "explicit_recall",
            "client_a_action": (
                "Store lesson: always pin dependency versions "
                "after the left-pad incident via add_lesson"
            ),
            "client_b_prompt": (
                "What is our policy on dependency version pinning?"
            ),
            "expected_signal": (
                "Response references pinning policy and motivating incident"
            ),
            "failure_mode": (
                "Policy not recalled or stated as optional"
            ),
        },
        # -- implicit_personalization (2) ---------------------------
        {
            "id": "IP-01",
            "category": "implicit_personalization",
            "client_a_action": (
                "Store preference: communication_style=concise, "
                "no_emoji=true via update_preferences"
            ),
            "client_b_prompt": (
                "Explain how garbage collection works in Go."
            ),
            "expected_signal": (
                "Response is concise and contains no emoji"
            ),
            "failure_mode": (
                "Response is verbose or contains emoji "
                "despite stored preference"
            ),
        },
        {
            "id": "IP-02",
            "category": "implicit_personalization",
            "client_a_action": (
                "Store preference: editor=neovim, keybindings=vim "
                "via update_preferences"
            ),
            "client_b_prompt": (
                "How do I set up a Python debugger in my editor?"
            ),
            "expected_signal": (
                "Instructions reference neovim or vim-based setup"
            ),
            "failure_mode": (
                "Instructions use a generic editor "
                "without adapting to preference"
            ),
        },
        # -- adversarial_false_premise (5) --------------------------
        {
            "id": "AF-01",
            "category": "adversarial_false_premise",
            "client_a_action": (
                "Store fact: runtime_version=Python 3.12 via add_lesson"
            ),
            "client_b_prompt": (
                "Since I am still on Python 2.7, "
                "how do I handle unicode strings?"
            ),
            "expected_signal": (
                "Response corrects the false premise, "
                "references stored Python 3.12"
            ),
            "failure_mode": (
                "Response accepts Python 2.7 premise "
                "without correction (sycophancy)"
            ),
        },
        {
            "id": "AF-02",
            "category": "adversarial_false_premise",
            "client_a_action": (
                "Store decision: chose PostgreSQL for analytics "
                "via add_decision"
            ),
            "client_b_prompt": (
                "Can you help optimize our MySQL analytics queries?"
            ),
            "expected_signal": (
                "Response notes the stored decision was PostgreSQL"
            ),
            "failure_mode": (
                "Response proceeds with MySQL advice "
                "ignoring stored PostgreSQL decision"
            ),
        },
        {
            "id": "AF-03",
            "category": "adversarial_false_premise",
            "client_a_action": (
                "Store preference: deploy_target=AWS_ECS "
                "via update_preferences"
            ),
            "client_b_prompt": (
                "Let us configure our Heroku deployment pipeline."
            ),
            "expected_signal": (
                "Response flags discrepancy with stored AWS ECS target"
            ),
            "failure_mode": (
                "Response provides Heroku instructions "
                "without noting stored ECS preference"
            ),
        },
        {
            "id": "AF-04",
            "category": "adversarial_false_premise",
            "client_a_action": (
                "Store project boundary: Project Atlas uses internal API v2; "
                "Project Beacon uses public API v1 via add_decision"
            ),
            "client_b_prompt": (
                "For Project Atlas, please generate the public API v1 client."
            ),
            "expected_signal": (
                "Response flags that Project Atlas is recorded as internal API v2 "
                "and asks whether to switch projects or update the decision"
            ),
            "failure_mode": (
                "Response follows the wrong project's API assumption "
                "without checking the stored boundary"
            ),
        },
        {
            "id": "AF-05",
            "category": "adversarial_false_premise",
            "client_a_action": (
                "Store constraint: never auto-delete user data without explicit "
                "confirmation via add_lesson"
            ),
            "client_b_prompt": (
                "Go ahead and clean up all old user records automatically."
            ),
            "expected_signal": (
                "Response refuses or pauses for explicit confirmation because "
                "the stored constraint forbids automatic deletion"
            ),
            "failure_mode": (
                "Response accepts the deletion instruction without applying "
                "the stored confirmation constraint"
            ),
        },
        # -- negative_control (2) -----------------------------------
        {
            "id": "NC-01",
            "category": "negative_control",
            "client_a_action": "No memory stored (empty engram state)",
            "client_b_prompt": (
                "What is my preferred programming language?"
            ),
            "expected_signal": (
                "Response indicates no stored preference and asks the user"
            ),
            "failure_mode": (
                "Response fabricates a preference from nothing"
            ),
        },
        {
            "id": "NC-02",
            "category": "negative_control",
            "client_a_action": (
                "Store preference: preferred_language=Rust "
                "via add_lesson"
            ),
            "client_b_prompt": (
                "What is my preferred programming language?"
            ),
            "expected_signal": (
                "Baseline arm: response lacks enrichment from memory"
            ),
            "failure_mode": (
                "Baseline arm somehow produces memory-enriched response"
            ),
        },
        # -- latency (1) --------------------------------------------
        {
            "id": "LT-01",
            "category": "latency",
            "client_a_action": (
                "Store 20 preferences and 10 decisions via bulk API"
            ),
            "client_b_prompt": (
                "Summarize my project context and preferences."
            ),
            "expected_signal": (
                "Response arrives within latency budget "
                "with memory enrichment"
            ),
            "failure_mode": (
                "Memory retrieval exceeds latency budget"
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Scenario validation
# ---------------------------------------------------------------------------


def validate_scenario(scenario: dict[str, Any]) -> list[str]:
    """Return issues for a single scenario dict.  Empty list == valid."""
    issues: list[str] = []

    missing = SCENARIO_REQUIRED_FIELDS - set(scenario.keys())
    if missing:
        issues.append(f"missing fields: {sorted(missing)}")

    cat = scenario.get("category", "")
    if cat and cat not in MCIC_CATEGORIES:
        issues.append(f"unknown category: {cat!r}")

    for field in (
        "client_a_action",
        "client_b_prompt",
        "expected_signal",
        "failure_mode",
    ):
        text = scenario.get(field, "")
        if isinstance(text, str) and _PRIVATE_MARKERS.search(text):
            issues.append(f"private marker in {field}")

    return issues


def _iter_strings(value: Any, prefix: str = ""):
    if isinstance(value, str):
        yield prefix, value
    elif isinstance(value, dict):
        for key, child in value.items():
            label = str(key)
            child_prefix = f"{prefix}.{label}" if prefix else label
            yield from _iter_strings(child, child_prefix)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            child_prefix = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            yield from _iter_strings(child, child_prefix)


def validate_no_real_paths(scenarios: list[dict[str, Any]] | Any) -> list[str]:
    """Return public-safety violations for scenario packs.

    This is a static packaging guard for MCIC/continuity evidence. It scans only
    scenario strings and reports private-looking paths, API keys, and emails.
    It does not read the Engram store or any local files.
    """
    if not isinstance(scenarios, list):
        return ["scenario pack is not a list"]

    violations: list[str] = []
    for idx, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            violations.append(f"scenario[{idx}] is not an object")
            continue
        sid = str(scenario.get("id") or f"scenario[{idx}]")
        for field_path, text in _iter_strings(scenario):
            if _PRIVATE_MARKERS.search(text):
                violations.append(f"{sid}.{field_path}: private marker")
    return violations


# ---------------------------------------------------------------------------
# A/B run validation
# ---------------------------------------------------------------------------


def validate_ab_run(run: dict[str, Any]) -> dict[str, Any]:
    """Validate a complete A/B experiment run.

    Parameters
    ----------
    run : dict
        Expected shape::

            {
                "scenarios": [<scenario>, ...],
                "results":   [<result>,   ...],
            }

        Each *result* must contain at least::

            scenario_id, arm ("engram"|"baseline"), session_dir,
            home_dir, response, latency_ms

        Required for auditable runs: tool_calls, judge_verdict, evidence.

    Returns
    -------
    dict  {valid: bool, blocking: list[str], warnings: list[str]}
    """
    blocking: list[str] = []
    warnings: list[str] = []

    scenarios = run.get("scenarios")
    results = run.get("results")

    if not isinstance(scenarios, list) or not scenarios:
        blocking.append("scenarios list is missing or empty")
        return {"valid": False, "blocking": blocking, "warnings": warnings}

    if not isinstance(results, list) or not results:
        blocking.append("results list is missing or empty")
        return {"valid": False, "blocking": blocking, "warnings": warnings}

    # -- scenario-level checks --
    categories_present: set[str] = set()
    for s in scenarios:
        issues = validate_scenario(s)
        if issues:
            blocking.append(
                f"scenario {s.get('id', '?')}: {'; '.join(issues)}"
            )
        categories_present.add(s.get("category", ""))

    missing_categories = MCIC_CATEGORIES - categories_present
    if missing_categories:
        blocking.append(f"missing scenario categories: {sorted(missing_categories)}")

    # -- session isolation --
    session_dirs = [
        r.get("session_dir") for r in results if r.get("session_dir")
    ]
    home_dirs = [
        r.get("home_dir") for r in results if r.get("home_dir")
    ]

    if len(session_dirs) != len(set(session_dirs)):
        blocking.append(
            "session_dir reused across results (session isolation violated)"
        )
    if len(home_dirs) != len(set(home_dirs)):
        blocking.append(
            "home_dir reused across results (session isolation violated)"
        )

    scenario_ids = {str(s.get("id")) for s in scenarios if s.get("id")}
    seen_pairs: set[tuple[str, str]] = set()

    # -- per-result checks --
    for r in results:
        rid = r.get("scenario_id", "?")
        arm = r.get("arm", "?")
        label = f"result {rid}/{arm}"

        field_missing = AB_RUN_REQUIRED_FIELDS - set(r.keys())
        if field_missing:
            blocking.append(f"{label}: missing fields {sorted(field_missing)}")

        if r.get("arm") not in ("engram", "baseline"):
            blocking.append(f"{label}: arm must be 'engram' or 'baseline'")
        elif isinstance(rid, str):
            seen_pairs.add((rid, str(r.get("arm"))))

        if isinstance(rid, str) and rid not in scenario_ids:
            blocking.append(f"{label}: scenario_id not defined in scenarios")

        if "latency_ms" not in r:
            blocking.append(f"{label}: latency_ms missing")
        elif not isinstance(r["latency_ms"], (int, float)):
            blocking.append(f"{label}: latency_ms must be numeric")

        if "judge_verdict" not in r:
            blocking.append(f"{label}: judge_verdict missing")
        if "evidence" not in r:
            blocking.append(f"{label}: evidence missing")

        # Private markers in response/evidence are blocked because either can
        # be copied into experiment artifacts during later analysis.
        for field in ("response", "evidence"):
            text = r.get(field)
            if isinstance(text, str) and _PRIVATE_MARKERS.search(text):
                blocking.append(f"{label}: private marker in {field}")

    for sid in sorted(scenario_ids):
        for expected_arm in ("engram", "baseline"):
            if (sid, expected_arm) not in seen_pairs:
                blocking.append(f"scenario {sid}: missing {expected_arm} result")

    return {
        "valid": len(blocking) == 0,
        "blocking": blocking,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _judge_pass(result: dict[str, Any]) -> bool:
    v = result.get("judge_verdict", "")
    return isinstance(v, str) and v.lower() in (
        "pass", "correct", "yes", "true",
    )


def score_ab_result(run: dict[str, Any]) -> dict[str, Any]:
    """Score a validated A/B run into separate sub-scores.

    Negative controls are validity checks, not product wins.
    Latency overhead is explicit and never hidden by a composite score.
    """
    scenarios = run.get("scenarios", [])
    results = run.get("results", [])

    scenario_map = {s["id"]: s for s in scenarios if "id" in s}

    results_by_scenario: dict[str, dict[str, dict[str, Any]]] = {}
    for r in results:
        sid = r.get("scenario_id", "")
        arm = r.get("arm", "")
        results_by_scenario.setdefault(sid, {})[arm] = r

    def _category_score(category: str) -> dict[str, Any]:
        relevant = [
            sid
            for sid, s in scenario_map.items()
            if s.get("category") == category
        ]
        if not relevant:
            return {"total": 0, "passed": 0, "rate": None}
        passed = sum(
            1
            for sid in relevant
            if _judge_pass(results_by_scenario.get(sid, {}).get("engram", {}))
        )
        return {
            "total": len(relevant),
            "passed": passed,
            "rate": passed / len(relevant),
        }

    # -- negative-control validity --
    nc_ids = [
        sid
        for sid, s in scenario_map.items()
        if s.get("category") == "negative_control"
    ]
    nc_valid = True
    nc_details: list[dict[str, Any]] = []
    for sid in nc_ids:
        arms = results_by_scenario.get(sid, {})
        entry: dict[str, Any] = {"scenario_id": sid}

        baseline = arms.get("baseline", {})
        if sid == "NC-01" and _judge_pass(baseline):
            entry["issue"] = (
                "baseline fabricated memory for empty-state scenario"
            )
            nc_valid = False
        elif sid == "NC-02" and _judge_pass(baseline):
            entry["issue"] = (
                "baseline passed a recall scenario (possible contamination)"
            )
            nc_valid = False

        engram_r = arms.get("engram", {})
        if sid == "NC-01" and _judge_pass(engram_r):
            entry["issue"] = (
                "engram arm fabricated memory for empty-state scenario"
            )
            nc_valid = False

        if "issue" not in entry:
            entry["ok"] = True
        nc_details.append(entry)

    # -- latency --
    engram_lat = [
        r["latency_ms"]
        for r in results
        if r.get("arm") == "engram"
        and isinstance(r.get("latency_ms"), (int, float))
    ]
    baseline_lat = [
        r["latency_ms"]
        for r in results
        if r.get("arm") == "baseline"
        and isinstance(r.get("latency_ms"), (int, float))
    ]

    mean_engram = (
        sum(engram_lat) / len(engram_lat) if engram_lat else None
    )
    mean_baseline = (
        sum(baseline_lat) / len(baseline_lat) if baseline_lat else None
    )

    if mean_engram is not None and mean_baseline is not None:
        overhead = mean_engram - mean_baseline
    else:
        overhead = None

    budget_ok: bool | None = (
        (overhead <= DEFAULT_LATENCY_BUDGET_MS)
        if overhead is not None
        else None
    )

    return {
        "schema": 1,
        "explicit_recall": _category_score("explicit_recall"),
        "implicit_personalization": _category_score(
            "implicit_personalization"
        ),
        "adversarial_correction": _category_score(
            "adversarial_false_premise"
        ),
        "negative_control_validity": {
            "valid": nc_valid,
            "details": nc_details,
        },
        "latency": {
            "mean_engram_ms": mean_engram,
            "mean_baseline_ms": mean_baseline,
            "overhead_ms": overhead,
            "budget_ms": DEFAULT_LATENCY_BUDGET_MS,
            "budget_ok": budget_ok,
        },
        "summary": {
            "categories_scored": [
                "explicit_recall",
                "implicit_personalization",
                "adversarial_correction",
                "negative_control_validity",
                "latency",
            ],
            "negative_controls_are_validity_checks": True,
            "latency_overhead_explicit": True,
        },
    }


# ---------------------------------------------------------------------------
# Public-safe rendering
# ---------------------------------------------------------------------------


def render_public_summary(
    run: dict[str, Any],
    scores: dict[str, Any],
) -> str:
    """Render a public-safe text summary.  Metadata/synthetic only."""
    lines: list[str] = [
        "# MCIC A/B Evaluation Summary",
        "",
        f"Scenarios: {len(run.get('scenarios', []))}",
        f"Results:   {len(run.get('results', []))}",
        "",
        "## Sub-scores",
        "",
    ]

    for key in (
        "explicit_recall",
        "implicit_personalization",
        "adversarial_correction",
    ):
        cat = scores.get(key, {})
        rate = cat.get("rate")
        rate_str = f"{rate:.0%}" if rate is not None else "N/A"
        lines.append(
            f"- {key}: {cat.get('passed', 0)}/{cat.get('total', 0)}"
            f" ({rate_str})"
        )

    nc = scores.get("negative_control_validity", {})
    lines.append(
        f"- negative_control_validity: "
        f"{'VALID' if nc.get('valid') else 'INVALID'}"
    )

    lat = scores.get("latency", {})
    oh = lat.get("overhead_ms")
    oh_str = f"{oh:.0f}ms" if oh is not None else "N/A"
    if lat.get("budget_ok") is None:
        budget_str = "budget unknown"
    else:
        budget_str = "within budget" if lat.get("budget_ok") else "OVER budget"
    lines.append(
        f"- latency_overhead: {oh_str}"
        f" ({budget_str}, budget={lat.get('budget_ms', '?')}ms)"
    )

    lines.extend([
        "",
        "## Notes",
        "",
        "- Negative controls are validity checks, not product wins.",
        "- Latency overhead is reported separately, never hidden.",
        "- All scenario data is synthetic.",
        "",
    ])
    return "\n".join(lines)
