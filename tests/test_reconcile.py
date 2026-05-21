"""reconcile_memories 自动对账功能测试。"""

import json
import tempfile
from pathlib import Path

from engram_core.core import Engram


def _make_engram(tmp_path: Path) -> Engram:
    return Engram(root=tmp_path)


def _write_memory_file(mem_dir: Path, name: str, content: str):
    """Create a fake Claude auto-memory .md file."""
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / name).write_text(content, encoding="utf-8")


def _make_claude_memory_dir(tmp_path: Path) -> Path:
    """Return a fake claude projects/*/memory dir."""
    mem_dir = tmp_path / "fake_claude" / "projects" / "test-project" / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir


# ── Basic import ──────────────────────────────────────────────────────

def test_reconcile_imports_new_memory(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "test_lesson.md", """\
---
name: Test Lesson
description: A test lesson
type: feedback
---

Always verify before deploying new features to production.
This prevents regression bugs from reaching users.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()

    assert result["scanned_files"] >= 1
    assert result["imported"] == 1
    assert "test_lesson.md" in result["sources"]


# ── Idempotency ──────────────────────────────────────────────────────

def test_reconcile_idempotent(tmp_path: Path):
    """Second run should import 0 items."""
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "lesson.md", """\
---
name: Important Lesson
type: feedback
---

Use integration tests not mocks for database validation.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]

    r1 = engram.reconcile_memories()
    assert r1["imported"] == 1

    r2 = engram.reconcile_memories()
    assert r2["imported"] == 0
    assert r2["duplicates"] >= 1


# ── Code fence lines are skipped ──────────────────────────────────────

def test_reconcile_skips_code_fence_files(tmp_path: Path):
    """Files whose only body content is inside code fences should not import garbage."""
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "code_only.md", """\
---
name: Code Example
type: project
---

```
some_code_here()
```
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()

    # Should not import — first meaningful line after skipping code fences
    # is "some_code_here()" which is fine, but let's check code fence itself
    # is not used as summary
    lessons = engram.get_lessons(limit=None)
    for l in lessons:
        assert l.get("summary", "").strip() != "```"


def test_reconcile_skips_short_content(tmp_path: Path):
    """Files with less than 5 chars of meaningful text should be skipped."""
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "tiny.md", """\
---
name: Tiny
type: feedback
---

OK
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert result["imported"] == 0


# ── MEMORY.md index file is excluded ──────────────────────────────────

def test_reconcile_skips_memory_index(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "MEMORY.md", """\
- [Lesson 1](lesson1.md) — important lesson
- [Lesson 2](lesson2.md) — another one
""")
    # No actual lesson files, only the index
    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert result["scanned_files"] == 0
    assert result["imported"] == 0


# ── Duplicate detection against existing lessons ──────────────────────

def test_reconcile_detects_existing_lesson_as_dup(tmp_path: Path):
    """If a lesson already exists in Engram, reconcile should not reimport."""
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    # Pre-add the lesson to Engram
    engram.add_lesson("Always verify before deploying features to production", domain="feedback")

    _write_memory_file(mem_dir, "deploy_lesson.md", """\
---
name: Deploy Lesson
type: feedback
---

Always verify before deploying features to production environment.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert result["imported"] == 0
    assert result["duplicates"] >= 1


# ── Duplicate detection against existing decisions ────────────────────

def test_reconcile_detects_existing_decision_as_dup(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    engram.add_decision(
        "Should we use DeepSeek API for testing",
        choice="Yes use DeepSeek API for all feature validation tests",
        reasoning="Need real LLM evaluation",
    )

    _write_memory_file(mem_dir, "deepseek.md", """\
---
name: DeepSeek Decision
type: project
---

Yes use DeepSeek API for all feature validation tests
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert result["imported"] == 0
    assert result["duplicates"] >= 1


# ── Multiple files at once ────────────────────────────────────────────

def test_reconcile_multiple_files(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "lesson_a.md", """\
---
name: Lesson A
type: feedback
---

Always run tests before merging pull requests into main branch.
""")

    _write_memory_file(mem_dir, "lesson_b.md", """\
---
name: Lesson B
type: project
---

The frontend uses React with TypeScript for type safety.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert result["scanned_files"] == 2
    assert result["imported"] == 2


# ── Domain mapping from frontmatter type ──────────────────────────────

def test_reconcile_domain_from_type(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "ref.md", """\
---
name: API Reference
type: reference
---

Pipeline bugs are tracked in Linear project INGEST for the data team.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    engram.reconcile_memories()

    lessons = engram.get_lessons(domain="reference", limit=None)
    assert len(lessons) >= 1
    assert "Linear" in lessons[0].get("summary", "")


# ── Empty directory returns zeros ─────────────────────────────────────

def test_reconcile_empty_dir(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)
    # No files created

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    result = engram.reconcile_memories()
    assert result == {"scanned_files": 0, "imported": 0, "duplicates": 0, "skipped_large": 0, "sources": []}


# ── Non-existent path doesn't crash ──────────────────────────────────

def test_reconcile_nonexistent_path(tmp_path: Path):
    engram = _make_engram(tmp_path / "engram")
    engram._CLAUDE_MEMORY_GLOBS = [str(tmp_path / "does_not_exist" / "*.md")]
    result = engram.reconcile_memories()
    assert result["scanned_files"] == 0
    assert result["imported"] == 0


# ══════════════════════════════════════════════════════════════════════
# AI config file scanning tests
# ══════════════════════════════════════════════════════════════════════

def _make_project_with_claude_md(tmp_path: Path, name: str, content: str) -> Path:
    """Create a fake project dir with a CLAUDE.md file."""
    project_dir = tmp_path / name
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "CLAUDE.md").write_text(content, encoding="utf-8")
    return project_dir


def test_config_scan_imports_sections(tmp_path: Path):
    """Config scan should extract meaningful sections from CLAUDE.md."""
    engram = _make_engram(tmp_path / "engram")
    project = _make_project_with_claude_md(tmp_path, "myproject", """\
# Project Rules

## Coding Standards
Always use type hints in all Python function signatures.

## Testing
Run pytest with coverage before every commit.
""")

    # Override discovery to return our fake project
    engram._discover_project_roots = lambda: [project]
    # Remove global config scan
    original_home = Path.home
    result = engram.reconcile_ai_configs()
    assert result["scanned_files"] >= 1
    assert result["imported"] >= 1


def test_config_scan_idempotent(tmp_path: Path):
    """Second config scan should import 0."""
    engram = _make_engram(tmp_path / "engram")
    project = _make_project_with_claude_md(tmp_path, "proj", """\
# Rules
## Style
Use black formatter for all Python code and isort for imports.
""")
    engram._discover_project_roots = lambda: [project]

    r1 = engram.reconcile_ai_configs()
    r2 = engram.reconcile_ai_configs()
    assert r2["imported"] == 0
    assert r2["duplicates"] >= 1


def test_config_scan_skips_short_sections(tmp_path: Path):
    """Sections with < 15 chars of meaningful text should be skipped."""
    engram = _make_engram(tmp_path / "engram")
    # Create a fake home without global CLAUDE.md
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    project = _make_project_with_claude_md(tmp_path, "proj", """\
# Rules
## A
OK.
""")
    engram._discover_project_roots = lambda: [project]
    # Patch to prevent scanning real global config
    import unittest.mock
    with unittest.mock.patch("pathlib.Path.home", return_value=fake_home):
        result = engram.reconcile_ai_configs()
    assert result["imported"] == 0


def test_parse_config_sections():
    """_parse_config_sections should split markdown by headers."""
    from engram_core.core import Engram
    sections = Engram._parse_config_sections("""\
# Top

## Section A
Content A line 1.
Content A line 2.

## Section B
Content B.
""", "test.md")
    titles = [s[0] for s in sections]
    assert "Section A" in titles
    assert "Section B" in titles
    assert len(sections) >= 2


# ══════════════════════════════════════════════════════════════════════
# Review page & apply_review tests
# ══════════════════════════════════════════════════════════════════════

def test_generate_review_page_html(tmp_path: Path):
    """generate_review_page should return valid HTML with knowledge items."""
    engram = _make_engram(tmp_path / "engram")
    engram.add_lesson("Test lesson for review", domain="testing")
    engram.add_decision("Test decision", choice="Option A", reasoning="Because")

    html = engram.generate_review_page(lang="zh")
    assert "<!DOCTYPE html>" in html
    assert "Test lesson for review" in html
    assert "Test decision" in html
    assert "Option A" in html
    assert "确认审查结果" in html


def test_generate_review_page_en(tmp_path: Path):
    """English review page should have English labels."""
    engram = _make_engram(tmp_path / "engram")
    engram.add_lesson("Something important", domain="general")

    html = engram.generate_review_page(lang="en")
    assert "Confirm Review" in html
    assert "Something important" in html


def test_export_review_page_creates_file(tmp_path: Path):
    """export_review_page should write an HTML file to exports dir."""
    engram = _make_engram(tmp_path / "engram")
    engram.add_lesson("Export test lesson", domain="testing")

    path = engram.export_review_page()
    assert path.exists()
    assert path.suffix == ".html"
    assert path.stat().st_size > 100


def test_apply_review_text_format(tmp_path: Path):
    """apply_review should archive items from text format input."""
    engram = _make_engram(tmp_path / "engram")
    lesson = engram.add_lesson("Lesson to archive", domain="test")
    lesson_id = lesson.get("id", "")

    review_text = f"archive lesson {lesson_id}"
    result = engram.apply_review(review_text)
    assert result["archived"] >= 1
    assert result["total_requested"] == 1


def test_apply_review_json_format(tmp_path: Path):
    """apply_review should archive items from JSON dict input."""
    engram = _make_engram(tmp_path / "engram")
    lesson = engram.add_lesson("JSON archive test", domain="test")
    lesson_id = lesson.get("id", "")

    review_data = {
        "action": "engram_review",
        "archive": [{"id": lesson_id, "type": "lesson"}],
    }
    result = engram.apply_review(review_data)
    assert result["archived"] >= 1


def test_apply_review_empty(tmp_path: Path):
    """apply_review with no items should return 0."""
    engram = _make_engram(tmp_path / "engram")
    result = engram.apply_review({"archive": []})
    assert result["archived"] == 0
    assert result["total_requested"] == 0


# ══════════════════════════════════════════════════════════════════════
# Rarity classification tests
# ══════════════════════════════════════════════════════════════════════

def test_rarity_decision_with_reasoning_is_epic_or_above(tmp_path: Path):
    """Decisions with detailed reasoning should be epic or legendary."""
    engram = _make_engram(tmp_path / "engram")
    item = {
        "id": "test1", "question": "Database choice",
        "choice": "PostgreSQL", "reasoning": "Better JSON support, and we need ACID for financial transactions",
        "domain": "architecture", "access_count": 3,
    }
    rarity = engram.classify_rarity(item, "decision")
    assert rarity in ("legendary", "epic")


def test_rarity_identity_keyword_boosts(tmp_path: Path):
    """Items with identity keywords should rank higher."""
    engram = _make_engram(tmp_path / "engram")
    item = {"id": "t2", "summary": "核心身份定位是产品负责人", "domain": "identity", "detail": "Long description about identity"}
    rarity = engram.classify_rarity(item, "lesson")
    assert rarity in ("legendary", "epic")


def test_rarity_staging_item_returns_staging(tmp_path: Path):
    """Staging items always return 'staging' regardless of content quality."""
    engram = _make_engram(tmp_path / "engram")
    item = {"id": "t3", "summary": "Some imported fact", "source_tool": "auto_reconcile",
            "domain": "auto_reconcile", "tier": "staging"}
    rarity = engram.classify_rarity(item, "lesson")
    assert rarity == "staging"


def test_rarity_verified_minimum_is_rare(tmp_path: Path):
    """Verified items always get at least 'rare' — no low-quality in verified knowledge."""
    engram = _make_engram(tmp_path / "engram")
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=90)).isoformat()
    item = {"id": "t4", "summary": "Ancient wisdom", "timestamp": old_date,
            "last_reviewed": old_date, "tier": "verified"}
    rarity = engram.classify_rarity(item, "lesson")
    assert rarity in ("legendary", "epic", "rare")


def test_review_page_contains_stars(tmp_path: Path):
    """Review page should contain star ratings."""
    engram = _make_engram(tmp_path / "engram")
    engram.add_lesson("Test lesson for stars", domain="testing")
    html = engram.generate_review_page(lang="zh")
    assert "★" in html
    assert "品质图例" in html


# ══════════════════════════════════════════════════════════════════════
# Tier system tests
# ══════════════════════════════════════════════════════════════════════

def test_only_three_verified_rarities(tmp_path: Path):
    """classify_rarity should only return legendary/epic/rare for verified items."""
    engram = _make_engram(tmp_path / "engram")
    valid = {"legendary", "epic", "rare"}
    # Simple verified item
    item = {"id": "v1", "summary": "Basic rule", "tier": "verified"}
    assert engram.classify_rarity(item, "lesson") in valid
    # High-value verified decision
    item2 = {"id": "v2", "question": "Core architecture",
             "choice": "Microservices", "reasoning": "Scalability needed for identity layer",
             "domain": "architecture", "tier": "verified"}
    assert engram.classify_rarity(item2, "decision") in valid


def test_staging_always_returns_staging(tmp_path: Path):
    """Even a high-quality item in staging tier returns 'staging' rarity."""
    engram = _make_engram(tmp_path / "engram")
    item = {"id": "s1", "summary": "核心身份定位是产品负责人",
            "domain": "identity", "detail": "Very detailed content",
            "tier": "staging", "access_count": 10}
    assert engram.classify_rarity(item, "lesson") == "staging"


def test_reconcile_imports_as_staging(tmp_path: Path):
    """Auto-reconciled items should have tier='staging'."""
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "new_lesson.md", """\
---
name: Auto Import
type: feedback
---

Always review PRs before merging to avoid breaking changes in production.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    engram.reconcile_memories()

    lessons = engram.get_lessons(limit=None)
    assert len(lessons) >= 1
    # Most recently added should be staging
    imported = [l for l in lessons if "review PRs" in l.get("summary", "")]
    assert len(imported) >= 1
    assert imported[0].get("tier") == "staging"


def test_promote_knowledge(tmp_path: Path):
    """promote_knowledge should change tier from staging to verified."""
    from engram_core.core import _read_json, _write_json
    engram = _make_engram(tmp_path / "engram")
    lesson = engram.add_lesson("Promotable lesson", domain="test")
    lesson_id = lesson.get("id", "")

    # Manually set to staging first
    lessons_path = engram._knowledge_dir / "lessons.json"
    data = _read_json(lessons_path)
    for entry in data:
        if entry.get("id") == lesson_id:
            entry["tier"] = "staging"
    _write_json(lessons_path, data)

    result = engram.promote_knowledge(lesson_id)
    assert result.get("status") == "promoted"

    # Verify it's now verified
    updated = engram.get_lessons(limit=None)
    promoted = [l for l in updated if l.get("id") == lesson_id]
    assert promoted[0]["tier"] == "verified"


def test_apply_review_with_promote(tmp_path: Path):
    """apply_review should handle promote list alongside archive."""
    from engram_core.core import _read_json, _write_json
    engram = _make_engram(tmp_path / "engram")
    lesson = engram.add_lesson("Promote via review", domain="test")
    lesson_id = lesson.get("id", "")

    # Set to staging
    lessons_path = engram._knowledge_dir / "lessons.json"
    data = _read_json(lessons_path)
    for entry in data:
        if entry.get("id") == lesson_id:
            entry["tier"] = "staging"
    _write_json(lessons_path, data)

    review_data = {
        "promote": [{"id": lesson_id, "type": "lesson"}],
        "archive": [],
    }
    result = engram.apply_review(review_data)
    assert result["promoted"] >= 1


# ══════════════════════════════════════════════════════════════════════
# Bug fix regression tests
# ══════════════════════════════════════════════════════════════════════

def test_truncation_protects_verified(tmp_path: Path):
    """When exceeding 200 lessons, staging items are evicted first."""
    from engram_core.core import _read_json
    engram = _make_engram(tmp_path / "engram")
    # Add 199 staging lessons
    for i in range(199):
        engram.add_lesson(f"Staging lesson number {i} with enough chars", domain="bulk", tier="staging")
    # Add 1 verified lesson (old, would be first to drop in naive truncation)
    engram.add_lesson("Critical verified knowledge that must survive truncation", domain="core")
    # Now add 2 more staging to exceed 200
    engram.add_lesson(f"Overflow staging lesson A extra padding", domain="bulk", tier="staging")
    engram.add_lesson(f"Overflow staging lesson B extra padding", domain="bulk", tier="staging")

    lessons_path = engram._knowledge_dir / "lessons.json"
    data = _read_json(lessons_path)
    assert len(data) <= 200
    # The verified lesson must survive
    summaries = [l.get("summary", "") for l in data]
    assert any("Critical verified knowledge" in s for s in summaries)


def test_review_page_no_access_count_side_effect(tmp_path: Path):
    """Generating review page should NOT increment access_count."""
    from engram_core.core import _read_json
    engram = _make_engram(tmp_path / "engram")
    engram.add_lesson("Side effect test lesson for review page", domain="test")

    lessons_path = engram._knowledge_dir / "lessons.json"
    before = _read_json(lessons_path)
    before_count = before[0].get("access_count", 0)

    # Generate review page (should not touch access_count)
    engram.generate_review_page()

    after = _read_json(lessons_path)
    after_count = after[0].get("access_count", 0)
    assert after_count == before_count


def test_frontmatter_horizontal_rule_not_swallowed(tmp_path: Path):
    """Content after a --- horizontal rule should not be skipped."""
    engram = _make_engram(tmp_path / "engram")
    mem_dir = _make_claude_memory_dir(tmp_path)

    _write_memory_file(mem_dir, "hr_test.md", """\
---
name: HR Test
type: feedback
---

Content before horizontal rule is important for testing.

---

Content after horizontal rule should also be captured correctly.
""")

    engram._CLAUDE_MEMORY_GLOBS = [str(mem_dir / "*.md")]
    engram.reconcile_memories()

    lessons = engram.get_lessons(limit=None)
    imported = [l for l in lessons if "horizontal rule" in l.get("summary", "").lower()
                or "horizontal rule" in l.get("detail", "").lower()]
    assert len(imported) >= 1


def test_archive_failure_not_counted_as_success(tmp_path: Path):
    """Archiving a non-existent item should not increment archived count."""
    engram = _make_engram(tmp_path / "engram")
    review_data = {
        "archive": [{"id": "nonexistent-id-12345", "type": "lesson"}],
        "promote": [],
    }
    result = engram.apply_review(review_data)
    assert result["archived"] == 0
    assert len(result.get("errors", [])) >= 1


def test_evaluate_tiers_promotes_by_access(tmp_path: Path):
    """evaluate_tiers should promote staging items with access_count >= 3."""
    from engram_core.core import _read_json, _write_json
    engram = _make_engram(tmp_path / "engram")
    lesson = engram.add_lesson("Frequently accessed staging item for test", domain="test", tier="staging")
    lesson_id = lesson.get("id", "")

    # Manually set access_count to 3
    lessons_path = engram._knowledge_dir / "lessons.json"
    data = _read_json(lessons_path)
    for entry in data:
        if entry.get("id") == lesson_id:
            entry["access_count"] = 3
    _write_json(lessons_path, data)

    result = engram.evaluate_tiers()
    assert result["promoted"] >= 1

    # Verify tier changed
    updated = _read_json(lessons_path)
    promoted = [l for l in updated if l.get("id") == lesson_id]
    assert promoted[0]["tier"] == "verified"


def test_decode_claude_project_name():
    """_decode_claude_project_name should handle multi-level paths."""
    from engram_core.core import Engram
    # Test with the actual project encoding we know works
    result = Engram._decode_claude_project_name("E--Personal-Intelligence-Identity-Asset")
    # Should resolve to a real path (or None if drive E doesn't exist)
    if result is not None:
        assert result.exists()
        assert "Personal" in str(result) or "personal" in str(result).lower()


# ── Staging backlog reminders ───────────────────────────────────────


def test_get_staging_summary(tmp_path: Path):
    """get_staging_summary should count active staging items."""
    from engram_core.core import _read_json, _write_json

    e = _make_engram(tmp_path)
    e.add_lesson("Auto imported alpha lesson pending review", domain="general")
    e.add_lesson("Zebra deployment note pending human confirmation", domain="python")
    e.add_decision(
        "Should staging beta decisions be reviewed",
        choice="Yes review before promotion",
        tier="staging",
    )

    lessons_path = tmp_path / "knowledge" / "lessons.json"
    lessons = _read_json(lessons_path)
    for entry in lessons:
        entry["tier"] = "staging"
    _write_json(lessons_path, lessons)

    summary = e.get_staging_summary()
    assert summary["total_staging"] == 3
    assert summary["staging_lessons"] == 2
    assert summary["staging_decisions"] == 1
    assert summary["oldest_staging"]


def test_staging_reminder_in_context(tmp_path: Path):
    """generate_context should warn when > 10 staging items."""
    from engram_core.core import _read_json, _write_json

    e = _make_engram(tmp_path)
    e._CLAUDE_MEMORY_GLOBS = []
    e._AI_GLOBAL_CONFIGS = []
    e._discover_project_roots = lambda: []
    summaries = [
        "alpha code review backlog marker",
        "bravo testing strategy backlog marker",
        "charlie deployment workflow backlog marker",
        "delta monitoring alert backlog marker",
        "echo documentation writing backlog marker",
        "foxtrot performance tuning backlog marker",
        "golf security audit backlog marker",
        "hotel refactor planning backlog marker",
        "india database migration backlog marker",
        "juliet cache policy backlog marker",
        "kilo logging governance backlog marker",
        "lima error handling backlog marker",
    ]
    for summary in summaries:
        e.add_lesson(summary, domain="general")

    path = tmp_path / "knowledge" / "lessons.json"
    data = _read_json(path)
    for entry in data:
        entry["tier"] = "staging"
    _write_json(path, data)

    ctx = e.generate_context()
    assert "staging_review_reminder" in ctx
    assert "12 条自动导入的知识尚未审核" in ctx


def test_no_staging_reminder_when_few(tmp_path: Path):
    """generate_context should NOT warn when <= 10 staging items."""
    from engram_core.core import _read_json, _write_json

    e = _make_engram(tmp_path)
    e._CLAUDE_MEMORY_GLOBS = []
    e._AI_GLOBAL_CONFIGS = []
    e._discover_project_roots = lambda: []
    e.add_lesson("自动导入经验关于代码审查", domain="general")

    path = tmp_path / "knowledge" / "lessons.json"
    data = _read_json(path)
    for entry in data:
        entry["tier"] = "staging"
    _write_json(path, data)

    ctx = e.generate_context()
    assert "staging_review_reminder" not in ctx


def test_wrap_up_session_reports_staging_reminder(tmp_path: Path, monkeypatch):
    """wrap_up_session should include staging_reminder when backlog exists."""
    import asyncio
    import engram_core.mcp_server as server

    e = _make_engram(tmp_path)
    e._CLAUDE_MEMORY_GLOBS = []
    e._AI_GLOBAL_CONFIGS = []
    e._discover_project_roots = lambda: []
    e.add_lesson("Session wrap staging reminder candidate", domain="general", tier="staging")
    monkeypatch.setattr(server, "_engram", e)

    raw = asyncio.run(server.wrap_up_session("Session ended with no durable new facts."))
    data = json.loads(raw)
    reminder = data.get("staging_reminder", {})
    assert reminder["total_staging"] == 1
    assert "待审知识" in reminder["message"]


def test_review_page_has_staging_filter_and_escapes_domain(tmp_path: Path):
    """Review page should expose staging filter controls and escape domain labels."""
    e = _make_engram(tmp_path)
    e.add_lesson(
        "Review page staging filter candidate",
        domain="<script>alert(1)</script>",
        tier="staging",
    )

    html = e.generate_review_page(lang="zh")
    assert "仅显示待审" in html
    assert "showStagingOnly" in html
    assert 'data-tier="staging"' in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# ── Conflict Detection ──────────────────────────────────────────────


def test_decision_conflict_detected():
    """Contradictory decisions in same domain → conflict warning in context."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e.add_decision("Python 测试框架选型", choice="使用 pytest 作为唯一测试框架", domain="python")
        e.add_decision("单元测试工具选型", choice="使用 unittest 标准库作为测试框架", domain="python")
        ctx = e.generate_context()
        assert "冲突" in ctx, "Should detect conflicting decisions"


def test_decision_no_false_positive():
    """Decisions on different topics in different domains → no conflict."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e.add_decision("API 框架选型", choice="FastAPI", domain="python")
        e.add_decision("数据库选型", choice="PostgreSQL", domain="database")
        ctx = e.generate_context()
        assert "知识冲突" not in ctx, "Should not flag unrelated decisions"


def test_decision_same_choice_no_conflict():
    """Decisions with similar questions but same choice → not a conflict."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e.add_decision("测试框架选型", choice="pytest", domain="python")
        e.add_decision("自动化测试工具", choice="pytest", domain="python")
        ctx = e.generate_context()
        assert "知识冲突" not in ctx, "Same choice should not be flagged"


def test_lesson_conflict_detected():
    """Contradictory lessons (affirm vs negate) on same topic → conflict."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e.add_lesson("Docker 容器化部署简单高效，推荐所有项目使用", domain="devops")
        e.add_lesson("Docker 增加复杂度和调试难度，简单项目不要用", domain="devops")
        ctx = e.generate_context()
        assert "冲突" in ctx, "Should detect contradictory lessons"


def test_lesson_no_false_positive():
    """Lessons on different topics → no conflict."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e.add_lesson("pytest 的 parametrize 支持数据驱动测试", domain="python")
        e.add_lesson("Docker 多阶段构建减少镜像体积", domain="devops")
        ctx = e.generate_context()
        assert "知识冲突" not in ctx, "Unrelated lessons should not be flagged"


# ── Token Budget Control ───────────────────────────────────────────


def test_estimate_tokens():
    """_estimate_tokens handles mixed CJK/ASCII."""
    from engram_core.core import Engram
    # Pure ASCII: ~1 token per 4 chars
    assert Engram._estimate_tokens("abcd") == 1
    assert Engram._estimate_tokens("abcdefgh") == 2
    # Pure CJK: ~1 token per char
    assert Engram._estimate_tokens("测试框架") == 4
    # Mixed
    assert Engram._estimate_tokens("pytest 测试") > 2


def test_max_tokens_none_includes_all():
    """max_tokens=None should include all sections (backward compat)."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        e.update_profile({"role": "test", "language": "zh"})
        e.add_lesson("避免同步阻塞操作影响 MCP 性能", domain="python")
        e.add_decision("框架选型", choice="FastAPI")

        ctx = e.generate_context()
        assert "关于用户" in ctx
        assert "相关经验教训" in ctx
        assert "已做的关键决策" in ctx


def test_max_tokens_tight_drops_low_priority():
    """Tight token budget should keep profile but drop low-priority sections."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        e.update_profile({"role": "developer", "language": "en"})
        e.add_lesson("避免同步阻塞操作影响 MCP 性能", domain="python")
        e.add_decision("框架选型", choice="FastAPI")

        ctx = e.generate_context(max_tokens=30)
        # Profile is highest priority, should always be included
        assert "关于用户" in ctx
        tokens = e._estimate_tokens(ctx)
        assert tokens <= 30


def test_max_tokens_large_equals_unlimited():
    """Large max_tokens should produce same output as None."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        e.update_profile({"role": "test"})
        e.add_lesson("Python asyncio TaskGroup 比 gather 更安全", domain="python")

        ctx_none = e.generate_context()
        ctx_large = e.generate_context(max_tokens=10000)
        assert ctx_none == ctx_large


# ── Token Budget Edge Cases ───────────────────────────────────────


def test_max_tokens_zero_returns_minimal():
    """max_tokens=0 or very small should still return something (not crash)."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        e.update_profile({"role": "test"})

        # Should not crash even with very tight budget
        ctx = e.generate_context(max_tokens=1)
        assert isinstance(ctx, str)


def test_max_tokens_profile_always_included():
    """Even with tight budget, profile section (highest priority) is included."""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        e.update_profile({"role": "senior engineer", "language": "en"})
        # Add many lessons to make context large
        for i in range(20):
            e.add_lesson(
                f"Unique lesson about topic number {i}: "
                f"{'performance' if i % 2 == 0 else 'security'} optimization technique",
                domain="python",
            )

        ctx = e.generate_context(max_tokens=50)
        assert "关于用户" in ctx or "senior engineer" in ctx


# ── Conflict Detection Edge Cases ─────────────────────────────────


def test_decision_conflict_cjk_questions():
    """冲突检测应正确处理纯中文问题和选择。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        # Questions must be different enough to pass dedup (bigram sim < 0.55)
        # but similar enough to trigger conflict (q_sim >= 0.25)
        # "项目中数据库的选择" vs "数据库技术选择方案" → sim=0.375
        e.add_decision("项目中数据库的选择", choice="PostgreSQL 关系型", domain="database")
        e.add_decision("数据库技术选择方案", choice="MongoDB 文档型", domain="database")

        ctx = e.generate_context()
        assert "知识冲突" in ctx, "Similar CJK questions with different choices should trigger conflict"


def test_lesson_conflict_negation_pairs():
    """教训冲突检测：肯定 vs 否定的配对应被检测到。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))
        e._CLAUDE_MEMORY_GLOBS = []
        e._AI_GLOBAL_CONFIGS = []
        e._discover_project_roots = lambda: []
        e.add_lesson("推荐使用类型注解提升代码可读性", domain="python")
        e.add_lesson("不推荐使用类型注解因为增加维护成本", domain="python")

        ctx = e.generate_context()
        assert "知识冲突" in ctx, "Affirm vs negate on same topic should trigger conflict"


# ── Reconcile Config Size Limit ───────────────────────────────────


def test_reconcile_ai_configs_skips_large_files():
    """reconcile_ai_configs 应跳过超过 50KB 的配置文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        e = _make_engram(Path(tmp))

        # Create a fake project root with a large CLAUDE.md
        project_root = Path(tmp) / "fake_project"
        project_root.mkdir()
        large_config = project_root / "CLAUDE.md"
        large_config.write_text("x" * 60_000, encoding="utf-8")

        # Patch _discover_project_roots to return our fake root
        e._discover_project_roots = lambda: [project_root]
        e._AI_GLOBAL_CONFIGS = []

        result = e.reconcile_ai_configs()
        assert result["imported"] == 0, "Large config file should be skipped"
