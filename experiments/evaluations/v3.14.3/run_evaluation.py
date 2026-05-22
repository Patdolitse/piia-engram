"""Run the v3.14.3 milestone evaluation against DeepSeek.

Reads the evidence pack + a curated bundle of source/test/doc files, sends one
big evaluation prompt per evaluator pass, parses the structured response, and
writes both raw JSON and a human-readable Markdown report.

USAGE
-----

    cd <repo root>
    PYTHONIOENCODING=utf-8 \\
        python experiments/evaluations/v3.14.3/run_evaluation.py

Reads the DeepSeek API key from ``experiments/benchmarks/round3/.env``
(re-used from the v3.13.2 evaluation).

OUTPUT
------

- ``results_<timestamp>.json``     — full API response, prompt, parsed scores
- ``REPORT.md``                    — human-readable consolidated report (overwrites)
- ``raw_log_<timestamp>.jsonl``    — one line per API call, for audit
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parents[2]
ENV_PATH = REPO_ROOT / "experiments" / "benchmarks" / "round3" / ".env"

# Materials the evaluator sees. Paths are relative to repo root.
# ``max_lines`` caps how much of each file we include — DeepSeek-chat has a
# 64K input window, so the full prompt must stay under ~50K tokens after
# accounting for system prompt and output budget.
EVIDENCE_FILES: tuple[tuple[str, str, int], ...] = (
    # (relative_path, label, max_lines)
    # — Curated docs: include in full —
    ("experiments/evaluations/v3.14.3/evidence_pack.md", "evidence_pack",       9999),
    ("docs/architecture.md",                             "architecture",        9999),
    ("docs/comparison.md",                               "comparison",          9999),
    ("docs/coverage_baseline_v3.14.2.md",                "coverage_baseline",   9999),
    # — CHANGELOG: only the recent v3.14.x entries —
    ("CHANGELOG.md",                                     "changelog",           120),
    # — README: top half is the positioning we want to evaluate —
    ("README.md",                                        "readme_en",           250),
    # — Core source: focus on facade + new modules; skip large HTML/CSS blobs —
    ("src/piia_engram/core.py",                          "core_py",             400),
    ("src/piia_engram/storage.py",                       "storage_py",          9999),
    ("src/piia_engram/crypto.py",                        "crypto_py",           9999),
    ("src/piia_engram/retrieval.py",                     "retrieval_py",        300),
    ("src/piia_engram/context.py",                       "context_py",          250),
    ("src/piia_engram/reconcile.py",                     "reconcile_py",        300),
    # reports.py is 1103 lines mostly HTML — only include the Python tier logic
    ("src/piia_engram/reports.py",                       "reports_py",          150),
    ("src/piia_engram/compat.py",                        "compat_py",           80),
    # mcp_server: focus on the recently-added safety code
    ("src/piia_engram/mcp_server.py",                    "mcp_server_py",       250),
    # — Tests: full files (each is small) —
    ("tests/test_crypto.py",                             "tests_crypto",        9999),
    ("tests/test_mcp_tools.py",                          "tests_mcp_tools",     9999),
    ("tests/test_review_page_xss.py",                    "tests_xss",           9999),
)

# How many independent evaluation passes to run (averages the variance)
DEFAULT_PASSES = 3


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_evidence() -> dict[str, str]:
    """Read every evidence file from disk, truncating to per-file ``max_lines``.

    Missing files become explicit ``<FILE NOT FOUND>`` placeholders so the
    evaluator can see the gap rather than silently get less material.
    """
    materials: dict[str, str] = {}
    for rel_path, label, max_lines in EVIDENCE_FILES:
        full = REPO_ROOT / rel_path
        if not full.is_file():
            materials[label] = f"<FILE NOT FOUND: {rel_path}>"
            continue
        text = full.read_text(encoding="utf-8")
        lines = text.splitlines()
        if len(lines) > max_lines:
            text = "\n".join(lines[:max_lines]) + (
                f"\n\n... [truncated for prompt budget — full file is {len(lines)} lines, "
                f"showing first {max_lines}]"
            )
        materials[label] = text
    return materials


SYSTEM_PROMPT = """你是一名资深软件工程评估专家。你的任务是评估一个本地优先的 AI 身份层项目 Engram 从 v3.13.2 到 v3.14.3 三个补丁版本的修复效果。

评估原则：
1. **严格基于证据** — 只引用提供给你的材料里实际存在的内容；不要凭印象判断。
2. **诚实于不足** — 项目方明确要求严厉批评，不要做客气评价。
3. **结构化输出** — 严格按要求的 JSON 格式返回，不要 markdown 包裹。
4. **比较纵向变化** — 重点不是"现在好不好"，而是"v3.13.2 的具体问题是否真的被修复"。"""


USER_PROMPT_TEMPLATE = """以下是 Engram v3.14.3 的评估材料。

---
{materials}
---

请严格按下面的 JSON schema 输出评估结果（直接返回 JSON，不要 markdown，不要解释）：

{{
  "scores": {{
    "architecture":   <0-10 整数>,
    "testing":        <0-10 整数>,
    "security":       <0-10 整数>,
    "documentation":  <0-10 整数>,
    "positioning":    <0-10 整数>,
    "overall":        <0-10 浮点，1 位小数>
  }},
  "verification_of_v3_13_2_issues": [
    {{
      "issue_id": "A",
      "summary": "core.py 4277 行必须拆分",
      "verdict": "fixed" | "partial" | "regression" | "unverified",
      "evidence": "具体引用 — 例如 'core.py 实际 1083 行，从 evidence_pack 二节获得'"
    }},
    // ... 对 A-U 中你能验证的逐项填写。verdict='unverified' 时也要写 evidence 解释为什么验证不了
  ],
  "answers_to_key_questions": {{
    "q1_architecture_complexity": "对第六节问题 1 的回答（200 字内，给具体观察）",
    "q2_coverage_honesty":        "对问题 2 的回答",
    "q3_pbkdf2_correctness":      "对问题 3 的回答",
    "q4_path_validation":         "对问题 4 的回答",
    "q5_doc_clarity":             "对问题 5 的回答 — 至少给出一个具体的文档对/错点",
    "q6_new_risks":               "对问题 6 的回答 — Mixin/重导出层/新模块依赖的新潜在问题",
    "q7_readme_confusion":        "对问题 7 的回答"
  }},
  "new_findings": [
    // v3.13.2 评估没提过的、本轮你新发现的问题，最重要的 3 个
    {{ "severity": "high"|"medium"|"low", "title": "...", "detail": "..." }},
    {{ "severity": "...", "title": "...", "detail": "..." }},
    {{ "severity": "...", "title": "...", "detail": "..." }}
  ],
  "next_3_priorities": [
    // 下一版本（v3.14.4 或 v3.15.0）最该做的 3 件事
    {{ "priority": 1, "title": "...", "why": "..." }},
    {{ "priority": 2, "title": "...", "why": "..." }},
    {{ "priority": 3, "title": "...", "why": "..." }}
  ],
  "evaluator_self_notes": "用一句话说明：本次评估你的最大不确定性是什么"
}}
"""


def build_user_prompt(materials: dict[str, str]) -> str:
    parts: list[str] = []
    for _, label, _max_lines in EVIDENCE_FILES:
        content = materials.get(label, f"<MISSING:{label}>")
        parts.append(f"=== {label} ===\n{content}")
    materials_text = "\n\n".join(parts)
    return USER_PROMPT_TEMPLATE.format(materials=materials_text)


def call_deepseek(
    system_prompt: str,
    user_prompt: str,
    *,
    max_retries: int = 3,
    timeout: int = 180,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Call DeepSeek chat completions. Returns (parsed_json, raw_response)."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set. Check that "
            f"{ENV_PATH} exists and contains the key."
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_text = resp.read().decode("utf-8")
            raw = json.loads(raw_text)
            content = raw["choices"][0]["message"]["content"]
            parsed = _extract_json(content)
            return parsed, raw
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            print(f"  attempt {attempt}/{max_retries} failed: {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
        except (KeyError, json.JSONDecodeError) as exc:
            last_error = exc
            print(f"  attempt {attempt}/{max_retries} bad response: {exc}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"DeepSeek call failed after {max_retries} retries: {last_error}")


def _extract_json(content: str) -> dict[str, Any]:
    """Pull the first JSON object out of the assistant's reply."""
    content = content.strip()
    # Strip optional markdown fence
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, flags=re.DOTALL)
    if fence:
        content = fence.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Try to find the largest brace-balanced substring
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start : end + 1])
        raise


def run_evaluation(passes: int = DEFAULT_PASSES) -> dict[str, Any]:
    load_env()
    materials = load_evidence()
    user_prompt = build_user_prompt(materials)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = EVAL_DIR / f"results_{timestamp}.json"
    log_path = EVAL_DIR / f"raw_log_{timestamp}.jsonl"

    all_results: list[dict[str, Any]] = []
    log_fh = log_path.open("w", encoding="utf-8")

    try:
        for i in range(1, passes + 1):
            print(f"=== pass {i}/{passes} ===", file=sys.stderr)
            parsed, raw = call_deepseek(SYSTEM_PROMPT, user_prompt)
            entry = {"pass": i, "parsed": parsed, "raw_meta": {
                "model": raw.get("model"),
                "usage": raw.get("usage"),
                "id": raw.get("id"),
            }}
            all_results.append(entry)
            log_fh.write(json.dumps({"pass": i, "raw": raw}, ensure_ascii=False) + "\n")
            log_fh.flush()
            print(f"  scores: {parsed.get('scores')}", file=sys.stderr)
    finally:
        log_fh.close()

    consolidated = {
        "timestamp": timestamp,
        "evaluator_model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "passes": passes,
        "results": all_results,
        "averages": _compute_averages(all_results),
    }

    results_path.write_text(
        json.dumps(consolidated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = render_report(consolidated)
    (EVAL_DIR / "REPORT.md").write_text(report, encoding="utf-8")
    print(f"\nDone. Wrote {results_path} and REPORT.md", file=sys.stderr)
    return consolidated


def _compute_averages(results: list[dict[str, Any]]) -> dict[str, float]:
    dims = ["architecture", "testing", "security", "documentation", "positioning", "overall"]
    averages: dict[str, float] = {}
    for d in dims:
        vals = []
        for r in results:
            scores = r.get("parsed", {}).get("scores") or {}
            v = scores.get(d)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        averages[d] = round(sum(vals) / len(vals), 2) if vals else 0.0
    return averages


def render_report(consolidated: dict[str, Any]) -> str:
    lines: list[str] = [
        "# Engram v3.14.3 — DeepSeek Milestone Evaluation",
        "",
        f"**Run timestamp**: {consolidated['timestamp']}",
        f"**Evaluator**: {consolidated['evaluator_model']} ({consolidated['passes']} passes)",
        "",
        "## Average scores",
        "",
        "| Dimension | v3.13.2 (5-evaluator avg) | v3.14.3 (this run) |",
        "|-----------|----------------------------|--------------------|",
    ]
    v313 = {
        "architecture": 5.4, "testing": 7.2, "security": 6.3,
        "documentation": 7.7, "positioning": 7.1, "overall": 6.9,
    }
    avg = consolidated["averages"]
    for dim in ["architecture", "testing", "security", "documentation", "positioning", "overall"]:
        lines.append(f"| {dim} | {v313[dim]} | **{avg[dim]}** |")

    lines.extend(["", "## Per-pass detail", ""])
    for r in consolidated["results"]:
        i = r["pass"]
        p = r.get("parsed", {})
        scores = p.get("scores", {})
        lines.append(f"### Pass {i}")
        lines.append("")
        lines.append(f"**Scores**: {json.dumps(scores, ensure_ascii=False)}")
        lines.append("")
        if "answers_to_key_questions" in p:
            lines.append("**Key Q&A**:")
            for k, v in p["answers_to_key_questions"].items():
                lines.append(f"- *{k}*: {v}")
            lines.append("")
        if "new_findings" in p:
            lines.append("**New findings**:")
            for nf in p["new_findings"]:
                lines.append(f"- [{nf.get('severity', '?')}] **{nf.get('title', '?')}** — {nf.get('detail', '')}")
            lines.append("")
        if "next_3_priorities" in p:
            lines.append("**Suggested next 3**:")
            for pri in p["next_3_priorities"]:
                lines.append(f"{pri.get('priority', '?')}. **{pri.get('title', '?')}** — {pri.get('why', '')}")
            lines.append("")
        notes = p.get("evaluator_self_notes")
        if notes:
            lines.append(f"**Evaluator's own uncertainty**: {notes}")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("## Raw")
    lines.append("")
    lines.append(f"See `results_{consolidated['timestamp']}.json` and `raw_log_{consolidated['timestamp']}.jsonl`.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    passes = DEFAULT_PASSES
    if len(sys.argv) > 1:
        try:
            passes = int(sys.argv[1])
        except ValueError:
            print(f"Usage: {sys.argv[0]} [passes]  (default {DEFAULT_PASSES})", file=sys.stderr)
            sys.exit(1)
    run_evaluation(passes=passes)
