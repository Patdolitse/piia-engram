"""Run the v3.16.0 milestone evaluation against DeepSeek.

Reads the evidence pack + a curated bundle of source/test/doc files, sends one
big evaluation prompt per evaluator pass, parses the structured response, and
writes both raw JSON and a human-readable Markdown report.

USAGE
-----

    cd <repo root>
    PYTHONIOENCODING=utf-8 \
        python experiments/evaluations/v3.16.0/run_evaluation.py [passes]

Reads the DeepSeek API key from ``experiments/benchmarks/round3/.env``.

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
EVIDENCE_FILES: tuple[tuple[str, str, int], ...] = (
    # (relative_path, label, max_lines)
    # — Curated evidence pack —
    ("experiments/evaluations/v3.16.0/evidence_pack.md", "evidence_pack",       9999),
    # — Architecture and comparison docs —
    ("docs/architecture.md",                             "architecture",        9999),
    ("docs/comparison.md",                               "comparison",          9999),
    # — CHANGELOG: v3.15.0 through v3.16.0 —
    ("CHANGELOG.md",                                     "changelog",           200),
    # — README: positioning —
    ("README.md",                                        "readme_en",           300),
    # — Core source modules —
    ("src/engram_core/core.py",                          "core_py",             400),
    ("src/engram_core/storage.py",                       "storage_py",          9999),
    ("src/engram_core/reports.py",                       "reports_hub_py",      9999),
    ("src/engram_core/reports_rarity.py",                "reports_rarity_py",   9999),
    ("src/engram_core/reports_identity.py",              "reports_identity_py", 9999),
    ("src/engram_core/reports_analytics.py",             "reports_analytics_py", 200),
    ("src/engram_core/telemetry.py",                     "telemetry_py",        9999),
    ("src/engram_core/mcp_server.py",                    "mcp_server_py",       350),
    # — Tests —
    ("tests/test_mcp_tools.py",                          "tests_mcp_tools",     9999),
    ("tests/test_mcp_coverage.py",                       "tests_mcp_coverage",  300),
    ("tests/test_telemetry.py",                          "tests_telemetry",     300),
    # — CONTRIBUTING (test baseline verification) —
    ("CONTRIBUTING.md",                                  "contributing",        60),
)

# How many independent evaluation passes to run
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
    """Read every evidence file from disk, truncating to per-file max_lines."""
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


SYSTEM_PROMPT = """你是一名资深软件工程评估专家。你的任务是评估一个本地优先的 AI 身份层项目 Engram 从 v3.14.3 到 v3.16.0 多个版本的改进效果。

评估原则：
1. **严格基于证据** — 只引用提供给你的材料里实际存在的内容；不要凭印象判断。
2. **诚实于不足** — 项目方明确要求严厉批评，不要做客气评价。
3. **结构化输出** — 严格按要求的 JSON 格式返回，不要 markdown 包裹。
4. **比较纵向变化** — 重点是"v3.14.3 的具体问题是否真的被修复，以及新功能是否引入新风险"。"""


USER_PROMPT_TEMPLATE = """以下是 Engram v3.16.0 的评估材料。

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
  "verification_of_v3_14_3_suggestions": [
    {{
      "item_id": "1",
      "summary": "建议内容",
      "verdict": "fixed" | "partial" | "not_addressed" | "unverified",
      "evidence": "具体引用"
    }}
  ],
  "answers_to_key_questions": {{
    "q1_architecture_complexity": "16 个源文件和两层 Mixin 嵌套是否过度？（200 字内）",
    "q2_test_quality": "490 个测试 83% 覆盖率——是否有过多浅层测试？",
    "q3_telemetry_security": "telemetry.py 的 payload 验证是否足够？",
    "q4_benchmark_rigor": "Round 10 43 case 100% 通过——门槛是否合理？",
    "q5_doc_maintenance": "新模块在文档中是否得到更新？",
    "q6_html_in_python": "520 行 HTML 在 Python 中——是否应考虑模板引擎？",
    "q7_version_strategy": "v3.14.3 到 v3.16.0 跳版本号是否清晰？"
  }},
  "new_findings": [
    {{ "severity": "high"|"medium"|"low", "title": "...", "detail": "..." }},
    {{ "severity": "...", "title": "...", "detail": "..." }},
    {{ "severity": "...", "title": "...", "detail": "..." }}
  ],
  "next_3_priorities": [
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
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", content, flags=re.DOTALL)
    if fence:
        content = fence.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
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
        "# Engram v3.16.0 — DeepSeek Milestone Evaluation",
        "",
        f"**Run timestamp**: {consolidated['timestamp']}",
        f"**Evaluator**: {consolidated['evaluator_model']} ({consolidated['passes']} passes)",
        "",
        "## Average scores",
        "",
        "| Dimension | v3.14.3 (DeepSeek 4-pass) | v3.16.0 (this run) |",
        "|-----------|----------------------------|--------------------|",
    ]
    v3143 = {
        "architecture": 7.50, "testing": 8.00, "security": 7.50,
        "documentation": 8.50, "positioning": 8.00, "overall": 7.90,
    }
    avg = consolidated["averages"]
    for dim in ["architecture", "testing", "security", "documentation", "positioning", "overall"]:
        lines.append(f"| {dim} | {v3143[dim]} | **{avg[dim]}** |")

    lines.extend(["", "## Per-pass detail", ""])
    for r in consolidated["results"]:
        i = r["pass"]
        p = r.get("parsed", {})
        scores = p.get("scores", {})
        lines.append(f"### Pass {i}")
        lines.append("")
        lines.append(f"**Scores**: {json.dumps(scores, ensure_ascii=False)}")
        lines.append("")
        if "verification_of_v3_14_3_suggestions" in p:
            lines.append("**Verification of v3.14.3 suggestions**:")
            for item in p["verification_of_v3_14_3_suggestions"]:
                lines.append(f"- [{item.get('verdict', '?')}] #{item.get('item_id', '?')}: {item.get('summary', '')} — {item.get('evidence', '')}")
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
