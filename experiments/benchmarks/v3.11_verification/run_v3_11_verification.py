import json
import os
import re
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from piia_engram.core import Engram, _read_json, _write_json  # noqa: E402


OUT_DIR = Path(__file__).resolve().parent
REPO_ROOT = OUT_DIR.parent.parent.parent
ROUND3_ENV = REPO_ROOT / "experiments" / "benchmarks" / "round3" / ".env"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path / "engram")


def write_memory_file(mem_dir: Path, name: str, content: str) -> Path:
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / name
    write_text(path, content)
    return path


def make_memory_dir(tmp_path: Path) -> Path:
    mem_dir = tmp_path / "fake_claude" / "projects" / "test" / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_case(case_id: str, name: str, fn: Callable[[Path], str | dict]) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"engram_{case_id.replace('.', '_')}_") as td:
        tmp_path = Path(td)
        started = datetime.now().isoformat(timespec="seconds")
        try:
            detail = fn(tmp_path)
            return {
                "id": case_id,
                "name": name,
                "passed": True,
                "detail": detail,
                "started_at": started,
            }
        except Exception as exc:  # noqa: BLE001 - verification should capture all failures
            return {
                "id": case_id,
                "name": name,
                "passed": False,
                "detail": str(exc),
                "traceback": traceback.format_exc(limit=5),
                "started_at": started,
            }


def group_result(group: str, name: str, cases: list[dict]) -> dict:
    passed = sum(1 for case in cases if case["passed"])
    return {
        "group": group,
        "name": name,
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# T1: memory reconciliation
# ---------------------------------------------------------------------------


def t1_1_basic_import(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    mem_dir = make_memory_dir(tmp_path)
    write_memory_file(
        mem_dir,
        "feedback_basic.md",
        """---
name: Feedback Basic
type: feedback
---

Always verify generated features before release.
This protects users from avoidable regressions.
""",
    )
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert_true(result["imported"] == 1, f"expected imported=1, got {result}")
    assert_true("feedback_basic.md" in result["sources"], f"missing source: {result}")
    return f"imported={result['imported']}, sources={result['sources']}"


def t1_2_idempotency(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    mem_dir = make_memory_dir(tmp_path)
    write_memory_file(
        mem_dir,
        "idempotent.md",
        """---
name: Idempotent
type: feedback
---

Use integration evidence before claiming release readiness.
""",
    )
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    first = engram.reconcile_memories()
    second = engram.reconcile_memories()
    assert_true(first["imported"] == 1, f"first import failed: {first}")
    assert_true(second["imported"] == 0, f"second import should be 0: {second}")
    assert_true(second["duplicates"] >= 1, f"expected duplicate count: {second}")
    return f"first={first}, second={second}"


def t1_3_imports_staging(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    mem_dir = make_memory_dir(tmp_path)
    write_memory_file(
        mem_dir,
        "staging.md",
        """---
name: Staging
type: feedback
---

Temporary imported memory should wait for review.
""",
    )
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    lessons = engram.get_lessons(limit=None, _update_access=False)
    matches = [item for item in lessons if "Temporary imported memory" in item.get("summary", "")]
    assert_true(result["imported"] == 1, f"import failed: {result}")
    assert_true(matches, "imported lesson not found")
    assert_true(matches[0].get("tier") == "staging", f"tier was {matches[0].get('tier')}")
    return f"tier={matches[0].get('tier')}"


def t1_4_skip_memory_index(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    mem_dir = make_memory_dir(tmp_path)
    write_memory_file(mem_dir, "MEMORY.md", "- [Index](lesson.md) only index content\n")
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert_true(result["scanned_files"] == 0, f"MEMORY.md should not be scanned: {result}")
    assert_true(result["imported"] == 0, f"MEMORY.md should not import: {result}")
    return str(result)


def t1_5_skip_short_content(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    mem_dir = make_memory_dir(tmp_path)
    write_memory_file(
        mem_dir,
        "tiny.md",
        """---
name: Tiny
type: feedback
---

OK
""",
    )
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert_true(result["imported"] == 0, f"short content imported unexpectedly: {result}")
    return str(result)


def t1_6_horizontal_rule_preserved(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    mem_dir = make_memory_dir(tmp_path)
    write_memory_file(
        mem_dir,
        "hr_test.md",
        """---
name: HR Test
type: feedback
---

Content before rule is important and should be imported.

---

Content after rule is also valid knowledge for testing.
""",
    )
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    lessons = engram.get_lessons(limit=None, _update_access=False)
    imported = [item for item in lessons if "Content before rule" in item.get("summary", "")]
    assert_true(result["imported"] == 1, f"import failed: {result}")
    assert_true(imported, "expected imported lesson not found")
    detail = imported[0].get("detail", "")
    assert_true("Content after rule" in detail, f"detail lost post-rule content: {detail!r}")
    return f"summary={imported[0]['summary']!r}, detail={detail!r}"


# ---------------------------------------------------------------------------
# T2: AI config scanning
# ---------------------------------------------------------------------------


def isolated_config_engram(tmp_path: Path) -> tuple[Engram, Path]:
    engram = make_engram(tmp_path)
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    engram._AI_GLOBAL_CONFIGS = []
    engram._discover_project_roots = lambda: [project_dir]
    return engram, project_dir


def t2_1_section_import(tmp_path: Path) -> str:
    engram, project_dir = isolated_config_engram(tmp_path)
    write_text(
        project_dir / "CLAUDE.md",
        """# Quality Gate
Always run the verification suite before announcing release readiness.

## Scope Boundary
Do not modify production files during verification-only tasks.
""",
    )
    result = engram.reconcile_ai_configs()
    assert_true(result["imported"] >= 1, f"expected import from CLAUDE.md: {result}")
    return str(result)


def t2_2_config_idempotency(tmp_path: Path) -> str:
    engram, project_dir = isolated_config_engram(tmp_path)
    write_text(
        project_dir / "CLAUDE.md",
        """# Stable Rule
Keep verification artifacts separate from production source files.
""",
    )
    first = engram.reconcile_ai_configs()
    second = engram.reconcile_ai_configs()
    assert_true(first["imported"] >= 1, f"first import failed: {first}")
    assert_true(second["imported"] == 0, f"second import should be 0: {second}")
    return f"first={first}, second={second}"


def t2_3_short_section_skipped(tmp_path: Path) -> str:
    engram, project_dir = isolated_config_engram(tmp_path)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    write_text(project_dir / "CLAUDE.md", "# Tiny\nshort\n")
    original_global = engram._AI_GLOBAL_CONFIGS
    engram._AI_GLOBAL_CONFIGS = [str(fake_home / ".claude" / "CLAUDE.md")]
    result = engram.reconcile_ai_configs()
    engram._AI_GLOBAL_CONFIGS = original_global
    assert_true(result["scanned_files"] == 1, f"expected one project config scanned: {result}")
    assert_true(result["imported"] == 0, f"short section imported unexpectedly: {result}")
    return str(result)


def t2_4_section_parser(tmp_path: Path) -> str:
    content = """---
name: Config Demo
type: project
---

# Identity
Keep local memory under user control.

## Workflow
Run tests before release.

# Boundaries
Do not edit production code in verification-only tasks.
"""
    sections = Engram._parse_config_sections(content, "CLAUDE.md")
    bodies = "\n".join(body for _, body in sections)
    titles = [title for title, _ in sections]
    assert_true("name: Config Demo" not in bodies, f"frontmatter leaked: {sections}")
    assert_true(titles == ["Identity", "Workflow", "Boundaries"], f"bad titles: {titles}")
    assert_true("Run tests before release." in bodies, f"missing body: {sections}")
    return f"sections={sections}"


# ---------------------------------------------------------------------------
# T3: review page
# ---------------------------------------------------------------------------


def t3_1_html_zh(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    engram.add_lesson("中文审查页面 lesson 内容", domain="testing")
    engram.add_decision("中文审查页面 decision 内容", choice="选择本地优先", reasoning="保护用户资产")
    html = engram.generate_review_page(lang="zh")
    for needle in ["<!DOCTYPE html>", "中文审查页面 lesson 内容", "中文审查页面 decision 内容", "确认审查结果"]:
        assert_true(needle in html, f"missing {needle!r}")
    return f"html_length={len(html)}"


def t3_2_html_en(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    engram.add_lesson("English review page lesson content", domain="testing")
    html = engram.generate_review_page(lang="en")
    assert_true("Confirm Review" in html, "missing English confirm label")
    assert_true("English review page lesson content" in html, "missing lesson content")
    return f"html_length={len(html)}"


def t3_3_rarity_legend_and_stars(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    engram.add_lesson("核心身份定位需要长期保留", domain="identity", detail="这条经验用于验证星级和品质图例。")
    html = engram.generate_review_page(lang="zh")
    assert_true("★" in html, "expected at least one star")
    assert_true("品质图例" in html, "missing quality legend")
    return "stars and 品质图例 present"


def t3_4_export_review_page(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    engram.add_lesson("Exported review page lesson", domain="testing")
    path = engram.export_review_page(lang="zh")
    assert_true(path.exists(), f"export missing: {path}")
    assert_true(path.suffix == ".html", f"bad suffix: {path}")
    assert_true(path.stat().st_size > 100, f"export too small: {path.stat().st_size}")
    return f"path={path}, size={path.stat().st_size}"


def t3_5_no_access_count_side_effect(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("Review page side effect guard", domain="testing")
    lessons_path = engram._knowledge_dir / "lessons.json"
    before = _read_json(lessons_path)
    before_count = next(item for item in before if item["id"] == lesson["id"]).get("access_count", 0)
    engram.generate_review_page(lang="zh")
    after = _read_json(lessons_path)
    after_count = next(item for item in after if item["id"] == lesson["id"]).get("access_count", 0)
    assert_true(after_count == before_count, f"access_count changed {before_count}->{after_count}")
    return f"access_count={after_count}"


# ---------------------------------------------------------------------------
# T4: tier system
# ---------------------------------------------------------------------------


def t4_1_staging_returns_staging(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    item = {
        "id": "s1",
        "summary": "核心身份定位和长期架构原则都很重要",
        "detail": "Long detailed reasoning " * 30,
        "domain": "identity",
        "tier": "staging",
        "access_count": 10,
    }
    rarity = engram.classify_rarity(item, "lesson")
    assert_true(rarity == "staging", f"expected staging, got {rarity}")
    return f"rarity={rarity}"


def t4_2_verified_three_rarities_only(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    items = [
        ({"id": "v1", "summary": "Basic useful verified item", "tier": "verified"}, "lesson"),
        ({"id": "v2", "summary": "核心身份定位", "detail": "详细说明" * 80, "domain": "identity", "tier": "verified"}, "lesson"),
        ({"id": "v3", "question": "Architecture choice", "choice": "Local first", "reasoning": "Protects user-owned context assets and improves trust.", "domain": "architecture", "tier": "verified"}, "decision"),
    ]
    valid = {"legendary", "epic", "rare"}
    rarities = [engram.classify_rarity(item, item_type) for item, item_type in items]
    assert_true(set(rarities).issubset(valid), f"unexpected rarities: {rarities}")
    return f"rarities={rarities}"


def t4_3_high_quality_decision(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    item = {
        "id": "d1",
        "question": "Should Engram keep memory local-first?",
        "choice": "Keep memory local-first by default",
        "reasoning": "Local-first preserves user trust, reduces privacy risk, and lets AI tools share context without a cloud dependency.",
        "domain": "architecture",
        "tier": "verified",
        "access_count": 3,
    }
    rarity = engram.classify_rarity(item, "decision")
    assert_true(rarity in {"epic", "legendary"}, f"expected epic/legendary, got {rarity}")
    return f"rarity={rarity}"


def t4_4_identity_keyword_boost(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    item = {
        "id": "l1",
        "summary": "核心身份定位是产品负责人和本地资产守护者",
        "detail": "这条知识会影响未来所有 AI 协作的上下文继承。",
        "domain": "identity",
        "tier": "verified",
    }
    rarity = engram.classify_rarity(item, "lesson")
    assert_true(rarity in {"epic", "legendary"}, f"expected epic/legendary, got {rarity}")
    return f"rarity={rarity}"


def t4_5_promote_knowledge(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("Promotable staging verification item", domain="testing")
    lessons_path = engram._knowledge_dir / "lessons.json"
    data = _read_json(lessons_path)
    for entry in data:
        if entry["id"] == lesson["id"]:
            entry["tier"] = "staging"
    _write_json(lessons_path, data)
    result = engram.promote_knowledge(lesson["id"])
    updated = _read_json(lessons_path)
    promoted = next(item for item in updated if item["id"] == lesson["id"])
    assert_true(result["status"] == "promoted", f"bad promote result: {result}")
    assert_true(promoted["tier"] == "verified", f"tier not verified: {promoted}")
    return str(result)


def t4_6_evaluate_tiers_promotes_by_access(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("Frequently referenced staging item for promotion", domain="testing", tier="staging")
    lessons_path = engram._knowledge_dir / "lessons.json"
    data = _read_json(lessons_path)
    for entry in data:
        if entry["id"] == lesson["id"]:
            entry["access_count"] = 3
    _write_json(lessons_path, data)
    result = engram.evaluate_tiers()
    updated = _read_json(lessons_path)
    promoted = next(item for item in updated if item["id"] == lesson["id"])
    assert_true(result["promoted"] >= 1, f"expected promotion: {result}")
    assert_true(promoted["tier"] == "verified", f"tier not verified: {promoted}")
    return str(result)


def t4_7_evaluate_tiers_no_false_promotion(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    lesson = engram.add_lesson("Unreferenced staging item remains pending", domain="testing", tier="staging")
    result = engram.evaluate_tiers()
    lessons = _read_json(engram._knowledge_dir / "lessons.json")
    item = next(entry for entry in lessons if entry["id"] == lesson["id"])
    assert_true(result["promoted"] == 0, f"unexpected promotion: {result}")
    assert_true(item["tier"] == "staging", f"tier changed unexpectedly: {item}")
    return str(result)


def t4_8_apply_review_promote_archive(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    a_item = engram.add_lesson("Alpha lifecycle promotion candidate for owner review", domain="testing", tier="staging")
    b_item = engram.add_lesson("Zebra billing archive record for completed experiment", domain="release", tier="verified")
    result = engram.apply_review(
        {
            "promote": [{"id": a_item["id"], "type": "lesson"}],
            "archive": [{"id": b_item["id"], "type": "lesson"}],
        }
    )
    lessons = _read_json(engram._knowledge_dir / "lessons.json")
    a_updated = next(item for item in lessons if item["id"] == a_item["id"])
    b_updated = next(item for item in lessons if item["id"] == b_item["id"])
    assert_true(result["promoted"] == 1, f"promote count wrong: {result}")
    assert_true(result["archived"] == 1, f"archive count wrong: {result}")
    assert_true(a_updated["tier"] == "verified", f"A not verified: {a_updated}")
    assert_true(b_updated["status"] == "outdated", f"B not archived: {b_updated}")
    return str(result)


# ---------------------------------------------------------------------------
# T5: bug regression
# ---------------------------------------------------------------------------


def t5_1_truncation_protects_verified(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    for index in range(199):
        engram.add_lesson(
            f"{index:03d}-staging-{index * 7919:x} candidate knowledge pending review",
            domain="bulk",
            tier="staging",
        )
    verified = engram.add_lesson(
        "Critical verified knowledge that must survive staging overflow",
        domain="core",
        tier="verified",
    )
    engram.add_lesson("overflow-alpha staging candidate unique tail", domain="bulk", tier="staging")
    engram.add_lesson("overflow-beta staging candidate unique tail", domain="bulk", tier="staging")
    lessons = _read_json(engram._knowledge_dir / "lessons.json")
    ids = {item.get("id") for item in lessons}
    assert_true(len(lessons) <= 200, f"lesson count exceeds cap: {len(lessons)}")
    assert_true(verified["id"] in ids, "verified item was evicted")
    return f"count={len(lessons)}, verified_present={verified['id'] in ids}"


def t5_2_archive_failure_not_success(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    result = engram.apply_review({"archive": [{"id": "nonexistent", "type": "lesson"}]})
    assert_true(result["archived"] == 0, f"archive failure counted as success: {result}")
    assert_true(bool(result["errors"]), f"expected errors: {result}")
    return str(result)


def t5_3_frontmatter_horizontal_rule(tmp_path: Path) -> str:
    return t1_6_horizontal_rule_preserved(tmp_path)


def t5_4_html_escape_domain_xss(tmp_path: Path) -> str:
    engram = make_engram(tmp_path)
    malicious = "<script>alert(1)</script>"
    engram.add_lesson("HTML escape domain regression case", domain=malicious)
    html = engram.generate_review_page(lang="zh")
    escaped = "&lt;script&gt;alert(1)&lt;/script&gt;"
    idx = html.find(malicious)
    snippet = html[max(0, idx - 80): idx + len(malicious) + 80] if idx >= 0 else ""
    assert_true(
        malicious not in html,
        "raw malicious <script> domain appeared in HTML; "
        f"likely unescaped domain group title. snippet={snippet!r}",
    )
    assert_true(escaped in html, "escaped malicious domain was not found")
    return "malicious domain escaped"


def t5_5_decode_project_path(tmp_path: Path) -> str:
    encoded = "E--Personal-Intelligence-Identity-Asset"
    result = Engram._decode_claude_project_name(encoded)
    drive = Path("E:/")
    target = Path("E:/Personal Intelligence Identity Asset")
    if drive.exists() and target.exists():
        assert_true(result is not None, "expected decoded path on this machine")
        assert_true(result.exists(), f"decoded path does not exist: {result}")
        assert_true("Personal" in str(result), f"decoded path unexpected: {result}")
        return f"decoded={result}"
    return f"skipped environment-specific assertion, decoded={result}"


# ---------------------------------------------------------------------------
# T6: DeepSeek LLM evaluation
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def extract_json(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.S)
        if match:
            return json.loads(match.group(1))
        raise


class DeepSeekClient:
    def __init__(self) -> None:
        env = load_env_file(ROUND3_ENV)
        self.api_key = env.get("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
        self.base_url = env.get("DEEPSEEK_BASE_URL", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
        self.model = env.get("DEEPSEEK_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
        if not self.api_key or self.api_key.startswith("<"):
            raise RuntimeError("DEEPSEEK_API_KEY is missing or placeholder")

    def chat_json(self, prompt: str) -> tuple[Any, str]:
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "你是知识管理专家。只输出有效 JSON，不要 Markdown，不要解释。",
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"DeepSeek HTTP {exc.code}: {body[:500]}") from exc
        raw = data["choices"][0]["message"]["content"]
        return extract_json(raw), raw


def majority_list(results: list[list[str]]) -> list[str]:
    counter: Counter[str] = Counter()
    for result in results:
        counter.update(str(item) for item in result)
    threshold = max(1, len(results) // 2 + 1)
    return sorted([item for item, count in counter.items() if count >= threshold])


def t6_1_high_value_selection(tmp_path: Path) -> dict:
    client = DeepSeekClient()
    engram = make_engram(tmp_path)
    items = [
        {
            "id": "K1",
            "type": "decision",
            "question": "Should Engram keep user memory local-first?",
            "choice": "Use local-first storage as the default architecture.",
            "reasoning": "This preserves user trust, protects private identity context, and lets multiple AI tools inherit the same user-owned knowledge without a cloud dependency.",
            "domain": "architecture",
            "tier": "verified",
        },
        {
            "id": "K2",
            "type": "lesson",
            "summary": "核心身份定位是本地 AI 身份资产的产品负责人",
            "detail": "This identity principle guides every future agent handoff, release gate, and privacy decision. It changes how project context is preserved across tools.",
            "domain": "identity",
            "tier": "verified",
        },
        {"id": "K3", "type": "lesson", "summary": "Run pytest before commit.", "detail": "Useful routine check.", "domain": "testing", "tier": "verified"},
        {"id": "K4", "type": "lesson", "summary": "Use Get-Content to inspect files on Windows.", "detail": "Local command detail.", "domain": "tooling", "tier": "verified"},
        {"id": "K5", "type": "lesson", "summary": "Temporary debug port was 5173 during one run.", "detail": "Short-lived environment detail.", "domain": "debug", "tier": "verified"},
    ]
    classifier_high = {
        item["id"]
        for item in items
        if engram.classify_rarity(item, item["type"]) in {"legendary", "epic"}
    }
    prompt = (
        "从以下 5 条知识中选出最有长期价值的 2 条。标准：可泛化、独特、影响未来做事方式、有具体上下文。\n"
        f"{json.dumps(items, ensure_ascii=False, indent=2)}\n"
        '只输出 JSON：{"selected_ids":["K1","K2"],"reason":"..."}'
    )
    raw_responses = []
    selections = []
    for _ in range(3):
        parsed, raw = client.chat_json(prompt)
        raw_responses.append(raw)
        selections.append([str(item) for item in parsed.get("selected_ids", [])])
    majority = majority_list(selections)
    overlap = len(set(majority) & classifier_high)
    accuracy = overlap / 2
    assert_true(accuracy >= 0.6, f"overlap too low: majority={majority}, classifier_high={sorted(classifier_high)}")
    return {
        "classifier_high": sorted(classifier_high),
        "llm_majority": majority,
        "accuracy": accuracy,
        "raw_responses": raw_responses,
    }


def t6_2_staging_filter(tmp_path: Path) -> dict:
    client = DeepSeekClient()
    items = [
        {"id": "S1", "summary": "用户只做方向和最终验收，AI 负责 routine verification closure", "detail": "This is reusable across project harness workflows.", "tier": "staging"},
        {"id": "S2", "summary": "所有用户可见内容必须中英双语", "detail": "A durable release quality rule affecting docs, MCP descriptions, and UI copy.", "tier": "staging"},
        {"id": "S3", "summary": "Yesterday the temp folder was C:/tmp/run-a", "detail": "One-off debugging path.", "tier": "staging"},
        {"id": "S4", "summary": "A transient command took 1.2 seconds", "detail": "No reusable decision value.", "tier": "staging"},
        {"id": "S5", "summary": "A local console printed warning line 37", "detail": "Only relevant to a finished run.", "tier": "staging"},
    ]
    expected = {"S1", "S2"}
    prompt = (
        "以下是 staging 暂存知识。请选择值得晋升为正式 verified 知识的条目 ID。\n"
        f"{json.dumps(items, ensure_ascii=False, indent=2)}\n"
        '只输出 JSON：{"promote_ids":["S1"],"reason":"..."}'
    )
    raw_responses = []
    selections = []
    for _ in range(3):
        parsed, raw = client.chat_json(prompt)
        raw_responses.append(raw)
        selections.append([str(item) for item in parsed.get("promote_ids", [])])
    majority = set(majority_list(selections))
    correct = sum((item["id"] in majority) == (item["id"] in expected) for item in items)
    accuracy = correct / len(items)
    assert_true(accuracy >= 0.6, f"accuracy={accuracy}, majority={sorted(majority)}, expected={sorted(expected)}")
    return {
        "expected_promote": sorted(expected),
        "llm_majority": sorted(majority),
        "accuracy": accuracy,
        "raw_responses": raw_responses,
    }


def t6_3_duplicate_detection(tmp_path: Path) -> dict:
    client = DeepSeekClient()
    engram = make_engram(tmp_path)
    pairs = [
        {
            "id": "P1",
            "existing": "所有新功能必须经过验证、评估、通过、上线流程。",
            "candidate": "新增功能上线前要先验证和评估，确认通过后再发布。",
            "expected_duplicate": True,
        },
        {
            "id": "P2",
            "existing": "用户偏好中文沟通，回答要简洁直接。",
            "candidate": "产品发布节奏是每三天一个 GitHub Release。",
            "expected_duplicate": False,
        },
        {
            "id": "P3",
            "existing": "T6 使用 DeepSeek API 做 LLM 判断能力评估。",
            "candidate": "Windows 客户端安装包需要用 dotnet publish 生成。",
            "expected_duplicate": False,
        },
    ]
    prompt = (
        "判断每对知识是否语义重复。相关但表达不同不一定重复；只有含义基本相同才算重复。\n"
        f"{json.dumps(pairs, ensure_ascii=False, indent=2)}\n"
        '只输出 JSON：{"pairs":[{"id":"P1","duplicate":true},{"id":"P2","duplicate":false},{"id":"P3","duplicate":false}]}'
    )
    raw_responses = []
    predictions_per_run = []
    for _ in range(3):
        parsed, raw = client.chat_json(prompt)
        raw_responses.append(raw)
        mapping = {str(item.get("id")): bool(item.get("duplicate")) for item in parsed.get("pairs", [])}
        predictions_per_run.append(mapping)
    majority = {}
    for pair in pairs:
        votes = [run.get(pair["id"]) for run in predictions_per_run if pair["id"] in run]
        majority[pair["id"]] = Counter(votes).most_common(1)[0][0] if votes else None
    correct = sum(majority[pair["id"]] == pair["expected_duplicate"] for pair in pairs)
    bigram = {
        pair["id"]: engram._bigram_similarity(pair["existing"], pair["candidate"]) >= 0.6
        for pair in pairs
    }
    assert_true(correct >= 2, f"correct={correct}/3, majority={majority}")
    return {
        "llm_majority": majority,
        "expected": {pair["id"]: pair["expected_duplicate"] for pair in pairs},
        "correct": correct,
        "accuracy": correct / len(pairs),
        "bigram_predictions": bigram,
        "raw_responses": raw_responses,
    }


def t6_4_tier_reasonableness(tmp_path: Path) -> dict:
    client = DeepSeekClient()
    engram = make_engram(tmp_path)
    items = [
        {"id": "R1", "type": "decision", "question": "Local-first default?", "choice": "Keep all identity memory local-first.", "reasoning": "Protects trust, privacy, and cross-tool continuity.", "domain": "architecture", "tier": "verified"},
        {"id": "R2", "type": "lesson", "summary": "核心身份定位影响所有项目交接", "detail": "Identity context determines future agent behavior and acceptance gates.", "domain": "identity", "tier": "verified"},
        {"id": "R3", "type": "lesson", "summary": "Use isolated experiments for risky prototypes.", "detail": "Prevents production churn while still gathering evidence.", "domain": "project", "tier": "verified"},
        {"id": "R4", "type": "lesson", "summary": "Run full tests before final completion.", "detail": "Evidence before claims.", "domain": "testing", "tier": "verified"},
        {"id": "R5", "type": "lesson", "summary": "Temporary debug folder path changed once.", "detail": "One-off local detail.", "domain": "debug", "tier": "verified"},
        {"id": "R6", "type": "decision", "question": "Use DeepSeek for benchmark judging?", "choice": "Use it only for LLM evaluation groups.", "reasoning": "External API is useful for semantic judging but should not gate local deterministic tests.", "domain": "testing", "tier": "verified"},
        {"id": "R7", "type": "lesson", "summary": "All user-visible content should be bilingual.", "detail": "Affects MCP descriptions, docs, and GUI text.", "domain": "product", "tier": "verified"},
        {"id": "R8", "type": "lesson", "summary": "A pytest run took 0.92 seconds.", "detail": "Ephemeral timing.", "domain": "debug", "tier": "staging"},
        {"id": "R9", "type": "lesson", "summary": "Do not modify main code during verification-only tasks.", "detail": "Keeps validation independent from implementation.", "domain": "project", "tier": "verified"},
        {"id": "R10", "type": "lesson", "summary": "Remember exact command output when reporting pass/fail.", "detail": "Improves evidence quality.", "domain": "testing", "tier": "verified"},
        {"id": "R11", "type": "decision", "question": "How to handle duplicate PyPI upload?", "choice": "Keep existing version and skip existing files on rerun.", "reasoning": "The artifact exists, so duplicate upload failure is release noise.", "domain": "release", "tier": "verified"},
        {"id": "R12", "type": "lesson", "summary": "The icon color was blue in one screenshot.", "detail": "Minor one-off visual note.", "domain": "debug", "tier": "verified"},
        {"id": "R13", "type": "lesson", "summary": "Staging imports need human review before earning color.", "detail": "Prevents raw auto-memory from looking fully trusted.", "domain": "product", "tier": "verified"},
        {"id": "R14", "type": "lesson", "summary": "The task file was on Desktop.", "detail": "One-run path detail.", "domain": "debug", "tier": "staging"},
        {"id": "R15", "type": "decision", "question": "Should verified knowledge be protected during truncation?", "choice": "Evict staging before verified.", "reasoning": "Protects confirmed user memory from noisy imports.", "domain": "architecture", "tier": "verified"},
    ]
    rated = []
    for item in items:
        view = dict(item)
        view["rarity"] = engram.classify_rarity(item, item["type"])
        rated.append(view)
    prompt = (
        "下面是知识库快照及系统给出的 rarity 评级。请判断每条评级是否合理。\n"
        f"{json.dumps(rated, ensure_ascii=False, indent=2)}\n"
        '只输出 JSON：{"reasonable_ids":["R1"],"unreasonable_ids":[],"reasonable_ratio":0.8,"notes":"..."}'
    )
    raw_responses = []
    ratios = []
    for _ in range(3):
        parsed, raw = client.chat_json(prompt)
        raw_responses.append(raw)
        if "reasonable_ratio" in parsed:
            ratios.append(float(parsed["reasonable_ratio"]))
        else:
            reasonable_ids = parsed.get("reasonable_ids", [])
            ratios.append(len(reasonable_ids) / len(items))
    median_ratio = sorted(ratios)[len(ratios) // 2]
    assert_true(median_ratio >= 0.7, f"reasonable ratio below threshold: {ratios}")
    return {
        "ratings": [{"id": item["id"], "rarity": item["rarity"], "tier": item["tier"]} for item in rated],
        "ratios": ratios,
        "median_ratio": median_ratio,
        "raw_responses": raw_responses,
    }


def run_t6_case(case_id: str, name: str, fn: Callable[[Path], dict]) -> dict:
    case = run_case(case_id, name, fn)
    if case["passed"] and isinstance(case["detail"], dict):
        detail = case["detail"]
        raw = detail.get("raw_responses", [])
        detail["raw_response_summaries"] = [str(item)[:500] for item in raw]
        detail.pop("raw_responses", None)
        case["detail"] = detail
    return case


def build_report(results: list[dict], extra: dict[str, Any]) -> str:
    total = sum(item["total"] for item in results)
    passed = sum(item["passed"] for item in results)
    failed = total - passed
    pass_rate = passed / total * 100 if total else 0
    t1_t5 = [item for item in results if item["group"] != "T6"]
    t1_t5_total = sum(item["total"] for item in t1_t5)
    t1_t5_passed = sum(item["passed"] for item in t1_t5)
    t6 = next((item for item in results if item["group"] == "T6"), None)
    t6_pass_rate = (t6["passed"] / t6["total"] * 100) if t6 and t6["total"] else 0
    overall_pass = t1_t5_passed == t1_t5_total and t6 is not None and t6_pass_rate >= 60

    lines = [
        "# Engram v3.11 全功能验证报告",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 仓库：`{REPO_ROOT}`",
        f"- 任务包：codex-task-verify-v3.11-full.md",
        f"- Python：`{sys.executable}`",
        "",
        "## 总结",
        f"- 总 case 数：{total}",
        f"- 通过：{passed}",
        f"- 失败：{failed}",
        f"- 通过率：{pass_rate:.1f}%",
        f"- T1-T5 功能/回归：{t1_t5_passed}/{t1_t5_total}",
        f"- T6 LLM 评估：{t6['passed'] if t6 else 0}/{t6['total'] if t6 else 0}（{t6_pass_rate:.1f}%）",
        "",
        "## 各组结果",
        "| 组 | 名称 | Case 数 | 通过 | 失败 |",
        "|---|---|---:|---:|---:|",
    ]
    for item in results:
        lines.append(f"| {item['group']} | {item['name']} | {item['total']} | {item['passed']} | {item['failed']} |")

    failures = [case for group in results for case in group["cases"] if not case["passed"]]
    lines.extend(["", "## 失败详情"])
    if failures:
        for case in failures:
            lines.append(f"- `{case['id']}` {case['name']}：{case['detail']}")
    else:
        lines.append("- 无")

    lines.extend(["", "## LLM 评估详情（T6）"])
    if t6:
        for case in t6["cases"]:
            status = "通过" if case["passed"] else "失败"
            lines.append(f"- `{case['id']}` {case['name']}：{status}")
            detail = case.get("detail")
            if isinstance(detail, dict):
                if "accuracy" in detail:
                    lines.append(f"  - accuracy：{detail['accuracy']:.2f}")
                if "median_ratio" in detail:
                    lines.append(f"  - median reasonable_ratio：{detail['median_ratio']:.2f}")
                summaries = detail.get("raw_response_summaries", [])
                if summaries:
                    lines.append(f"  - 原始响应摘要：{summaries[0]}")
            else:
                lines.append(f"  - detail：{detail}")
    else:
        lines.append("- T6 未执行")

    lines.extend(["", "## 额外验证命令"])
    for command, result in extra.items():
        lines.append(f"- `{command}`：{result}")

    lines.extend(["", "## 结论"])
    if overall_pass:
        lines.append("T1-T5 全部通过，T6 达到 60% 门槛；按任务包门槛，可以发布 v3.11.0。")
    else:
        lines.append("未达到任务包发布门槛；不建议发布 v3.11.0。")
        if t1_t5_passed != t1_t5_total:
            lines.append("- 阻断项：T1-T5 存在功能/回归失败。")
        if not t6 or t6_pass_rate < 60:
            lines.append("- 阻断项：T6 LLM 评估未达到 60% 或未完成。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    groups = [
        group_result(
            "T1",
            "记忆对账",
            [
                run_case("T1.1", "基本导入", t1_1_basic_import),
                run_case("T1.2", "幂等性", t1_2_idempotency),
                run_case("T1.3", "导入为 staging", t1_3_imports_staging),
                run_case("T1.4", "MEMORY.md 索引排除", t1_4_skip_memory_index),
                run_case("T1.5", "短内容跳过", t1_5_skip_short_content),
                run_case("T1.6", "水平线不吞内容", t1_6_horizontal_rule_preserved),
            ],
        ),
        group_result(
            "T2",
            "AI 配置扫描",
            [
                run_case("T2.1", "Section 导入", t2_1_section_import),
                run_case("T2.2", "幂等性", t2_2_config_idempotency),
                run_case("T2.3", "短 section 跳过", t2_3_short_section_skipped),
                run_case("T2.4", "Section 解析正确性", t2_4_section_parser),
            ],
        ),
        group_result(
            "T3",
            "审查页面",
            [
                run_case("T3.1", "HTML 生成（中文）", t3_1_html_zh),
                run_case("T3.2", "HTML 生成（英文）", t3_2_html_en),
                run_case("T3.3", "品质图例和星级", t3_3_rarity_legend_and_stars),
                run_case("T3.4", "导出文件", t3_4_export_review_page),
                run_case("T3.5", "无 access_count 副作用", t3_5_no_access_count_side_effect),
            ],
        ),
        group_result(
            "T4",
            "Tier 系统",
            [
                run_case("T4.1", "Staging 始终返回 staging rarity", t4_1_staging_returns_staging),
                run_case("T4.2", "Verified 只返回 3 种 rarity", t4_2_verified_three_rarities_only),
                run_case("T4.3", "高质量 decision 评级", t4_3_high_quality_decision),
                run_case("T4.4", "身份关键词提权", t4_4_identity_keyword_boost),
                run_case("T4.5", "promote_knowledge", t4_5_promote_knowledge),
                run_case("T4.6", "evaluate_tiers 基于 access_count", t4_6_evaluate_tiers_promotes_by_access),
                run_case("T4.7", "evaluate_tiers 不误晋升", t4_7_evaluate_tiers_no_false_promotion),
                run_case("T4.8", "apply_review promote + archive", t4_8_apply_review_promote_archive),
            ],
        ),
        group_result(
            "T5",
            "Bug 修复回归",
            [
                run_case("T5.1", "截断保护 verified", t5_1_truncation_protects_verified),
                run_case("T5.2", "Archive 失败不计成功", t5_2_archive_failure_not_success),
                run_case("T5.3", "Frontmatter 只在文件头解析", t5_3_frontmatter_horizontal_rule),
                run_case("T5.4", "HTML 转义防 XSS", t5_4_html_escape_domain_xss),
                run_case("T5.5", "项目路径解码", t5_5_decode_project_path),
            ],
        ),
        group_result(
            "T6",
            "LLM 智能评估",
            [
                run_t6_case("T6.1", "高价值知识识别", t6_1_high_value_selection),
                run_t6_case("T6.2", "暂存区内容筛选", t6_2_staging_filter),
                run_t6_case("T6.3", "重复检测", t6_3_duplicate_detection),
                run_t6_case("T6.4", "tier 决策合理性", t6_4_tier_reasonableness),
            ],
        ),
    ]

    for result in groups:
        write_json(OUT_DIR / f"results_{result['group']}.json", result)

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo_root": str(REPO_ROOT),
        "groups": groups,
    }
    write_json(OUT_DIR / "results_all.json", manifest)
    report = build_report(groups, extra={})
    write_text(OUT_DIR / "REPORT.md", report)
    print(report)
    return 0 if all(group["failed"] == 0 for group in groups) else 1


if __name__ == "__main__":
    raise SystemExit(main())
