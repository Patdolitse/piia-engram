"""D5: Stale/Conflict Detection — 7 tests (2 expected failures).

Verify stale warnings trigger correctly and document the conflict gap.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from engram_core.core import Engram, _read_json, _write_json

from .fixtures import (
    create_conflicting_decisions,
    create_lessons_only,
    get_lesson_ids,
    patch_lesson_timestamps,
)


def run_d5() -> dict[str, Any]:
    cases = [
        _d5_stale_warn_01,
        _d5_stale_nowarn_01,
        _d5_stale_threshold_01,
        _d5_stale_threshold_02,
        _d5_conflict_01,
        _d5_conflict_02,
        _d5_outdated_01,
    ]
    results = []
    for fn in cases:
        with tempfile.TemporaryDirectory(prefix="r10-d5-") as tmp:
            r = fn(Path(tmp))
            results.append(r)
    known_issues = [r for r in results if r.get("known_issue")]
    gated = [r for r in results if not r.get("known_issue")]
    return {
        "dimension": "D5",
        "name": "Stale/Conflict Detection",
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "gated_correct": sum(1 for r in gated if r["correct"]),
        "gated_total": len(gated),
        "passed": sum(1 for r in gated if r["correct"]) >= 5,
        "known_issues": [r["id"] for r in known_issues],
        "results": results,
    }


def _result(case_id: str, correct: bool, detail: str = "",
            known_issue: bool = False) -> dict:
    r = {"id": case_id, "correct": correct, "detail": detail}
    if known_issue:
        r["known_issue"] = True
    return r


_UNIQUE_STALE_SUMMARIES = [
    "避免在循环中做字符串拼接用 join 替代",
    "数据库连接池大小等于 CPU 核数乘以二加磁盘数",
    "React useEffect 的 cleanup 函数必须取消异步请求",
    "Docker 多阶段构建可以减少镜像体积百分之六十",
    "缓存失效策略用 TTL 加上事件驱动 invalidation",
    "微服务间通信优先用异步消息队列而不是同步调用",
    "pytest fixture scope 设为 function 避免状态泄漏",
    "API 版本控制用 URL 前缀而不是 header 标识",
    "FastAPI 的 depends 注入在测试中用 override 替换",
    "用 pathlib 替代 os.path 处理文件路径更 Pythonic",
]


def _make_stale_lessons(tmp: Path, count: int) -> Engram:
    """Create N lessons (distinct summaries) and make them all 45-day stale."""
    lessons_data = [
        {"summary": _UNIQUE_STALE_SUMMARIES[i], "domain": "general"}
        for i in range(count)
    ]
    e = create_lessons_only(tmp, lessons_data)
    ids = get_lesson_ids(e)
    patch_lesson_timestamps(tmp, ids, days_ago=45)
    return e


def _d5_stale_warn_01(tmp: Path) -> dict:
    """6 stale lessons → warning in context."""
    e = _make_stale_lessons(tmp, 6)
    ctx = e.generate_context()
    has_warning = "stale_knowledge_warning" in ctx
    return _result("D5-STALE-WARN-01", has_warning,
                    f"warning={has_warning}")


def _d5_stale_nowarn_01(tmp: Path) -> dict:
    """4 stale lessons → no warning."""
    e = _make_stale_lessons(tmp, 4)
    ctx = e.generate_context()
    no_warning = "stale_knowledge_warning" not in ctx
    return _result("D5-STALE-NOWARN-01", no_warning,
                    f"no_warning={no_warning}")


def _d5_stale_threshold_01(tmp: Path) -> dict:
    """Exactly 5 stale → no warning (threshold is > 5)."""
    e = _make_stale_lessons(tmp, 5)
    ctx = e.generate_context()
    no_warning = "stale_knowledge_warning" not in ctx
    return _result("D5-STALE-THRESHOLD-01", no_warning,
                    f"no_warning={no_warning}, count=5")


def _d5_stale_threshold_02(tmp: Path) -> dict:
    """Exactly 6 stale → warning present."""
    e = _make_stale_lessons(tmp, 6)
    ctx = e.generate_context()
    has_warning = "stale_knowledge_warning" in ctx
    return _result("D5-STALE-THRESHOLD-02", has_warning,
                    f"warning={has_warning}, count=6")


def _d5_conflict_01(tmp: Path) -> dict:
    """Contradictory decisions (pytest vs unittest) → conflict warning in context."""
    e = create_conflicting_decisions(tmp)
    ctx = e.generate_context()
    has_pytest = "pytest" in ctx
    has_unittest = "unittest" in ctx
    has_conflict_warning = "冲突" in ctx or "conflict" in ctx.lower()
    return _result("D5-CONFLICT-01", has_conflict_warning,
                    f"pytest={has_pytest}, unittest={has_unittest}, "
                    f"conflict_warning={has_conflict_warning}")


def _d5_conflict_02(tmp: Path) -> dict:
    """Contradictory lessons in same domain → conflict warning in context."""
    e = Engram(tmp)
    e.add_lesson("Docker 容器化部署简单高效，推荐所有项目使用", domain="devops")
    e.add_lesson("Docker 增加复杂度和调试难度，简单项目不要用", domain="devops")
    ctx = e.generate_context()
    has_conflict_warning = "冲突" in ctx or "conflict" in ctx.lower()
    return _result("D5-CONFLICT-02", has_conflict_warning,
                    f"conflict_warning={has_conflict_warning}")


def _d5_outdated_01(tmp: Path) -> dict:
    """Archived lessons should not appear in context."""
    e = Engram(tmp)
    lesson = e.add_lesson("This lesson should be archived and invisible", domain="test")
    e.archive_knowledge(lesson["id"])
    ctx = e.generate_context()
    visible = "archived and invisible" in ctx
    return _result("D5-OUTDATED-01", not visible,
                    f"archived_visible={visible}")


if __name__ == "__main__":
    import json
    result = run_d5()
    print(json.dumps(result, indent=2, ensure_ascii=False))
