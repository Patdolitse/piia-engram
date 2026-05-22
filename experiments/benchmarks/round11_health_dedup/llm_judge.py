"""Round 11 — DeepSeek V4 Pro LLM evaluation for health score & suggest_merges.

Evaluates whether the new v3.20.0 features produce useful, actionable output
by having DeepSeek V4 Pro judge the quality of responses.

Usage:
    python -m experiments.benchmarks.round11_health_dedup.llm_judge
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp

# Ensure project root on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from piia_engram.core import Engram

ENV_PATH = Path(__file__).resolve().parent.parent / "round3" / ".env"
MODEL_JUDGE = "deepseek-v4-pro"


def _load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _call_deepseek(messages: list[dict], api_key: str) -> str:
    """Call DeepSeek V4 Pro API and return the response content."""
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": MODEL_JUDGE,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 1024,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise


def _build_test_knowledge(tmp_dir: str) -> Engram:
    """Build a realistic test knowledge base for evaluation."""
    eng = Engram(root=Path(tmp_dir))

    # Add diverse lessons across multiple domains
    eng.add_lesson("Always use type hints in Python function signatures", "python")
    eng.add_lesson("Use pytest fixtures instead of setUp/tearDown", "python,testing")
    eng.add_lesson("Prefer composition over inheritance in Go", "go")
    eng.add_lesson("Use context.Context for cancellation propagation", "go")
    eng.add_lesson("Index foreign keys in PostgreSQL for join performance", "database")
    eng.add_lesson("Use connection pooling to avoid exhausting DB connections", "database")
    eng.add_lesson("Write semantic commit messages following Conventional Commits", "git")
    eng.add_lesson("Use feature branches and squash merge for clean history", "git")
    eng.add_lesson("Configure CORS headers explicitly, never use wildcard in production", "security")
    eng.add_lesson("Store secrets in environment variables, never in source code", "security")

    # Add decisions
    eng.add_decision("API 框架选型", "FastAPI", "异步支持好，自动生成文档")
    eng.add_decision("数据库选型", "PostgreSQL", "ACID 合规，扩展性强")
    eng.add_decision("CI/CD 平台", "GitHub Actions", "与代码仓库集成，免费额度够用")

    # Add near-duplicates by writing directly (to bypass dedup)
    lessons_path = Path(tmp_dir) / "knowledge" / "lessons.json"
    data = json.loads(lessons_path.read_text(encoding="utf-8"))

    dup1 = dict(data[0])
    dup1["id"] = "dup-type-hints"
    dup1["summary"] = "Always add type annotations to Python function parameters"
    data.append(dup1)

    dup2 = dict(data[4])
    dup2["id"] = "dup-foreign-keys"
    dup2["summary"] = "Create indexes on foreign key columns in PostgreSQL for join speed"
    data.append(dup2)

    # Add some stale items
    stale_time = (datetime.now() - timedelta(days=60)).isoformat()
    data[6]["last_reviewed"] = stale_time
    data[7]["last_reviewed"] = stale_time

    # Add a staging item
    staging = dict(data[2])
    staging["id"] = "staging-item"
    staging["tier"] = "staging"
    staging["summary"] = "Consider using goroutines with bounded concurrency"
    data.append(staging)

    eng._atomic_write(lessons_path, data)

    return eng


SCENARIOS = [
    {
        "id": "S1_health_score_actionability",
        "name": "Health score provides actionable insights",
        "description": "Evaluate whether the health score breakdown helps a user understand what to improve in their knowledge base",
        "judge_prompt": (
            "You are evaluating the output of a knowledge management tool's health report.\n\n"
            "The tool computed a health score for a user's knowledge base. Below is the JSON output.\n\n"
            "Evaluate on these criteria (1-5 each):\n"
            "1. ACTIONABILITY: Can the user understand what to improve from the dimensions?\n"
            "2. ACCURACY: Do the dimension values seem reasonable given the data description?\n"
            "3. COMPLETENESS: Are the four dimensions (freshness, quality, coverage, cleanliness) sufficient?\n\n"
            "Data description: 10 lessons across 5 domains (python, testing, go, database, git, security), "
            "2 near-duplicates added, 2 stale items (60 days), 1 staging item, 3 decisions.\n\n"
            "Respond with JSON: {\"actionability\": N, \"accuracy\": N, \"completeness\": N, \"reasoning\": \"...\"}"
        ),
    },
    {
        "id": "S2_suggest_merges_quality",
        "name": "Suggest merges returns useful merge recommendations",
        "description": "Evaluate whether suggest_merges correctly identifies duplicates and provides useful merge commands",
        "judge_prompt": (
            "You are evaluating a knowledge deduplication tool's output.\n\n"
            "The tool scanned a knowledge base and suggested merge candidates. Below is the JSON output.\n\n"
            "Evaluate on these criteria (1-5 each):\n"
            "1. PRECISION: Are the suggested duplicates truly similar enough to merge?\n"
            "2. ACTIONABILITY: Does each suggestion provide enough info to decide?\n"
            "3. ORDERING: Are suggestions ordered by relevance (highest similarity first)?\n\n"
            "Known ground truth: There are exactly 2 near-duplicate pairs:\n"
            "- 'type hints in function signatures' vs 'type annotations to function parameters'\n"
            "- 'Index foreign keys for join performance' vs 'indexes on foreign key columns for join speed'\n\n"
            "Respond with JSON: {\"precision\": N, \"actionability\": N, \"ordering\": N, \"reasoning\": \"...\"}"
        ),
    },
    {
        "id": "S3_health_vs_merges_consistency",
        "name": "Health score cleanliness matches suggest_merges output",
        "description": "Verify that health score's cleanliness dimension is consistent with suggest_merges findings",
        "judge_prompt": (
            "You are evaluating consistency between two features of a knowledge management tool.\n\n"
            "Feature 1: Health report with a 'cleanliness' dimension (0-100, lower = more duplicates/stale).\n"
            "Feature 2: suggest_merges that finds near-duplicate items.\n\n"
            "Below are both outputs. Evaluate consistency (1-5 each):\n"
            "1. CONSISTENCY: Does the cleanliness score reflect the number of duplicates found?\n"
            "2. COMPLETENESS: Does suggest_merges find all duplicates that health report detected?\n"
            "3. COHERENCE: Do the two features tell a consistent story about knowledge quality?\n\n"
            "Respond with JSON: {\"consistency\": N, \"completeness\": N, \"coherence\": N, \"reasoning\": \"...\"}"
        ),
    },
]


def run_evaluation():
    """Run the full LLM evaluation."""
    _load_env()
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found in environment or round3/.env")
        sys.exit(1)

    print(f"=== Round 11: Health Score & Suggest Merges Evaluation ===")
    print(f"Model: {MODEL_JUDGE}")
    print(f"Time: {datetime.now().isoformat()}")
    print()

    # Build test knowledge base
    tmp_dir = mkdtemp(prefix="engram_r11_")
    eng = _build_test_knowledge(tmp_dir)

    # Generate feature outputs
    health_report = eng.get_health_report()
    suggest_result = eng.suggest_merges(threshold=0.4)
    overview = eng.get_knowledge_overview(section="all")

    outputs = {
        "health_report": health_report,
        "suggest_merges": suggest_result,
        "overview": overview,
    }

    print(f"Health score: {health_report['health_score']}/100")
    print(f"Dimensions: {json.dumps(health_report['dimensions'])}")
    print(f"Merge candidates: {suggest_result['total_candidates']}")
    print()

    results = []
    total_score = 0
    max_score = 0

    for scenario in SCENARIOS:
        print(f"--- {scenario['name']} ---")

        # Build context for judge
        if scenario["id"] == "S1_health_score_actionability":
            feature_output = json.dumps(health_report, ensure_ascii=False, indent=2)
        elif scenario["id"] == "S2_suggest_merges_quality":
            feature_output = json.dumps(suggest_result, ensure_ascii=False, indent=2)
        else:
            feature_output = json.dumps({
                "health_report": health_report,
                "suggest_merges": suggest_result,
            }, ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": scenario["judge_prompt"]},
            {"role": "user", "content": f"Tool output:\n```json\n{feature_output}\n```"},
        ]

        try:
            response = _call_deepseek(messages, api_key)
            # Parse JSON from response
            # Strip markdown fences if present
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                clean = clean.strip()

            judgment = json.loads(clean)
            scores = {k: v for k, v in judgment.items() if k != "reasoning"}
            scenario_total = sum(scores.values())
            scenario_max = len(scores) * 5
            total_score += scenario_total
            max_score += scenario_max

            print(f"  Scores: {scores}")
            print(f"  Total: {scenario_total}/{scenario_max}")
            print(f"  Reasoning: {judgment.get('reasoning', 'N/A')[:200]}")

            results.append({
                "scenario": scenario["id"],
                "name": scenario["name"],
                "scores": scores,
                "total": scenario_total,
                "max": scenario_max,
                "reasoning": judgment.get("reasoning", ""),
            })
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "scenario": scenario["id"],
                "name": scenario["name"],
                "error": str(e),
            })

        print()

    # Summary
    print("=" * 60)
    print(f"OVERALL: {total_score}/{max_score} ({100*total_score/max_score:.0f}%)" if max_score else "NO SCORES")
    print()

    # Determine pass/fail (70% threshold)
    passed = max_score > 0 and (total_score / max_score) >= 0.70
    print(f"RESULT: {'PASS' if passed else 'FAIL'} (threshold: 70%)")

    # Save results
    result_path = Path(__file__).parent / "results_r11.json"
    result_data = {
        "timestamp": datetime.now().isoformat(),
        "model": MODEL_JUDGE,
        "version": "3.20.0",
        "health_score": health_report["health_score"],
        "dimensions": health_report["dimensions"],
        "merge_candidates": suggest_result["total_candidates"],
        "total_score": total_score,
        "max_score": max_score,
        "pass_rate": round(total_score / max_score * 100, 1) if max_score else 0,
        "passed": passed,
        "scenarios": results,
    }
    result_path.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults saved: {result_path}")

    return passed


if __name__ == "__main__":
    success = run_evaluation()
    sys.exit(0 if success else 1)
