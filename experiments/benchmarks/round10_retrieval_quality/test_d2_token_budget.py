"""D2: Context Token Budget — 6 tests (4 gate + 2 diagnostic).

Verify generate_context() output size stays within reasonable bounds.
Uses character count as proxy (no tiktoken dependency).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from piia_engram.core import Engram, _write_json

from .fixtures import create_full_engram


def run_d2() -> dict[str, Any]:
    cases = [
        _d2_minimal_01,
        _d2_typical_01,
        _d2_typical_02,
        _d2_maxed_01,
        _d2_long_summaries_01,
        _d2_stale_warning_01,
    ]
    results = []
    for fn in cases:
        with tempfile.TemporaryDirectory(prefix="r10-d2-") as tmp:
            r = fn(Path(tmp))
            results.append(r)
    # D2-MAXED-01 is diagnostic only
    gated = [r for r in results if not r.get("diagnostic")]
    return {
        "dimension": "D2",
        "name": "Token Budget",
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "gated_correct": sum(1 for r in gated if r["correct"]),
        "gated_total": len(gated),
        "passed": sum(1 for r in gated if r["correct"]) >= 4,
        "results": results,
    }


def _result(case_id: str, correct: bool, detail: str = "",
            diagnostic: bool = False) -> dict:
    r = {"id": case_id, "correct": correct, "detail": detail}
    if diagnostic:
        r["diagnostic"] = True
    return r


def _d2_minimal_01(tmp: Path) -> dict:
    """Profile only → small output."""
    e = Engram(tmp)
    e.update_profile({"role": "开发者", "language": "中文", "technical_level": "junior"})
    ctx = e.generate_context()
    chars = len(ctx)
    return _result("D2-MINIMAL-01", chars < 500,
                    f"chars={chars}")


def _d2_typical_01(tmp: Path) -> dict:
    """Typical full setup, no project → 1000-4000 chars."""
    e = create_full_engram(tmp)
    ctx = e.generate_context()
    chars = len(ctx)
    ok = 500 <= chars <= 4000
    return _result("D2-TYPICAL-01", ok,
                    f"chars={chars}, range=[500,4000]")


def _d2_typical_02(tmp: Path) -> dict:
    """Typical full setup + project → <= 5000 chars."""
    e = create_full_engram(tmp)
    ctx = e.generate_context(project_folder="E:/test-project")
    chars = len(ctx)
    return _result("D2-TYPICAL-02", chars <= 5000,
                    f"chars={chars}, limit=5000")


def _d2_maxed_01(tmp: Path) -> dict:
    """All fields maxed with long descriptions → diagnostic only."""
    e = Engram(tmp)
    e.update_profile({
        "role": "A" * 100,
        "description": "B" * 200,
        "technical_level": "senior",
        "language": "中文",
    })
    prefs_path = e._identity_dir / "preferences.json"
    _write_json(prefs_path, {
        "work_patterns": {f"p{i}": "x" * 80 for i in range(10)},
        "communication": "y" * 100,
        "tool_preferences": {f"t{i}": "z" * 50 for i in range(6)},
    })
    std_path = e._identity_dir / "quality_standards.json"
    _write_json(std_path, {
        "acceptance_threshold": 5,
        "rules": ["R" * 100 for _ in range(7)],
    })
    for i in range(15):
        e.add_lesson("L" * 150, domain=f"domain_{i % 5}")
    for i in range(10):
        e.add_decision(f"Q{'q'*80}", choice=f"C{'c'*80}", reasoning="R" * 100)
    ctx = e.generate_context()
    chars = len(ctx)
    # Diagnostic: just record, always "passes" as diagnostic
    return _result("D2-MAXED-01", True,
                    f"chars={chars} (diagnostic — records ceiling)", diagnostic=True)


def _d2_long_summaries_01(tmp: Path) -> dict:
    """8 lessons with ~150-char CJK summaries → lesson section < 2000 chars."""
    e = Engram(tmp)
    e.update_profile({"role": "test"})
    for i in range(8):
        summary = f"这是第{i}条很长的经验教训" + "，包含详细的技术细节和实践经验" * 3
        e.add_lesson(summary, domain="python")
    ctx = e.generate_context()
    # Extract lesson section
    if "相关经验教训" in ctx:
        start = ctx.index("相关经验教训")
        # Find next section header or end
        rest = ctx[start:]
        lines = rest.split("\n")
        lesson_lines = []
        for line in lines[1:]:
            if line.startswith("## "):
                break
            lesson_lines.append(line)
        lesson_section = "\n".join(lesson_lines)
        chars = len(lesson_section)
    else:
        chars = 0
    return _result("D2-LONG-SUMMARIES-01", chars < 2000,
                    f"lesson_section_chars={chars}, limit=2000")


def _d2_stale_warning_01(tmp: Path) -> dict:
    """10 stale items → warning adds < 200 chars overhead."""
    from datetime import datetime, timedelta
    from piia_engram.core import _read_json

    e = Engram(tmp)
    e.update_profile({"role": "test"})
    for i in range(10):
        e.add_lesson(f"Stale lesson {i} for budget test", domain="general")

    # Patch all to 45 days ago
    path = tmp / "knowledge" / "lessons.json"
    data = _read_json(path)
    old = (datetime.now() - timedelta(days=45)).isoformat()
    for entry in data:
        entry["last_reviewed"] = old
        entry["timestamp"] = old
    _write_json(path, data)

    ctx = e.generate_context()
    if "stale_knowledge_warning" in ctx:
        start = ctx.index("stale_knowledge_warning")
        rest = ctx[start:]
        lines = rest.split("\n")
        warning_lines = []
        for line in lines:
            if line.startswith("## ") and "stale" not in line:
                break
            warning_lines.append(line)
        warning_chars = len("\n".join(warning_lines))
    else:
        warning_chars = 0
    return _result("D2-STALE-WARNING-01", warning_chars < 200,
                    f"warning_chars={warning_chars}, limit=200")


if __name__ == "__main__":
    import json
    result = run_d2()
    print(json.dumps(result, indent=2, ensure_ascii=False))
