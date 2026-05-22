"""D1: Context Assembly Completeness — 8 deterministic tests.

Verify generate_context() includes all expected sections and
respects truncation limits.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from piia_engram.core import Engram, _write_json

from .fixtures import create_full_engram


def run_d1() -> dict[str, Any]:
    cases = [
        _d1_section_01,
        _d1_section_02,
        _d1_section_03,
        _d1_section_04,
        _d1_profile_01,
        _d1_prefs_01,
        _d1_decisions_01,
        _d1_lessons_01,
    ]
    results = []
    for fn in cases:
        with tempfile.TemporaryDirectory(prefix="r10-d1-") as tmp:
            r = fn(Path(tmp))
            results.append(r)
    return {
        "dimension": "D1",
        "name": "Context Assembly Completeness",
        "total": len(results),
        "correct": sum(1 for r in results if r["correct"]),
        "passed": all(r["correct"] for r in results),
        "results": results,
    }


def _result(case_id: str, correct: bool, detail: str = "") -> dict:
    return {"id": case_id, "correct": correct, "detail": detail}


# ── Test cases ──────────────────────────────────────────────────────


def _d1_section_01(tmp: Path) -> dict:
    """Full data + project_folder → all 7 sections present."""
    e = create_full_engram(tmp)
    ctx = e.generate_context(project_folder="E:/test-project")
    required = ["关于用户", "工作偏好", "质量标准", "经验领域", "相关经验教训", "已做的关键决策", "当前项目历史"]
    missing = [s for s in required if s not in ctx]
    return _result("D1-SECTION-01", len(missing) == 0,
                    f"missing: {missing}" if missing else "all 7 sections present")


def _d1_section_02(tmp: Path) -> dict:
    """Full data, no project_folder → 6 sections (no project history)."""
    e = create_full_engram(tmp)
    ctx = e.generate_context()
    has_project = "当前项目历史" in ctx
    has_core = all(s in ctx for s in ["关于用户", "工作偏好", "质量标准", "经验领域"])
    return _result("D1-SECTION-02", has_core and not has_project,
                    f"has_core={has_core}, has_project={has_project}")


def _d1_section_03(tmp: Path) -> dict:
    """Empty profile → warning message present."""
    e = Engram(tmp)
    ctx = e.generate_context()
    has_warning = "身份画像未设置" in ctx
    return _result("D1-SECTION-03", has_warning,
                    f"warning_present={has_warning}")


def _d1_section_04(tmp: Path) -> dict:
    """Profile + preferences only → only those 2 sections."""
    e = Engram(tmp)
    e.update_profile({"role": "测试员", "language": "中文"})
    prefs_path = e._identity_dir / "preferences.json"
    _write_json(prefs_path, {"communication": "简洁"})
    ctx = e.generate_context()
    has_user = "关于用户" in ctx
    has_prefs = "工作偏好" in ctx
    no_lessons = "相关经验教训" not in ctx
    no_decisions = "已做的关键决策" not in ctx
    ok = has_user and has_prefs and no_lessons and no_decisions
    return _result("D1-SECTION-04", ok,
                    f"user={has_user}, prefs={has_prefs}, no_lessons={no_lessons}, no_decisions={no_decisions}")


def _d1_profile_01(tmp: Path) -> dict:
    """Profile with trust boundary restricted field → role absent."""
    e = create_full_engram(tmp)
    # Set trust boundary to restrict role
    tb_path = e._identity_dir / "trust_boundaries.json"
    _write_json(tb_path, {"restricted_fields": ["role"]})
    ctx = e.generate_context()
    # get_safe_profile should strip restricted fields
    has_role_line = "角色: 全栈工程师" in ctx
    return _result("D1-PROFILE-01", not has_role_line,
                    f"role_visible={has_role_line}")


def _d1_prefs_01(tmp: Path) -> dict:
    """10 work_patterns → at most 8 appear."""
    e = Engram(tmp)
    e.update_profile({"role": "test"})
    prefs_path = e._identity_dir / "preferences.json"
    patterns = {f"pattern_{i}": f"value_{i}" for i in range(10)}
    _write_json(prefs_path, {"work_patterns": patterns})
    ctx = e.generate_context()
    count = sum(1 for i in range(10) if f"pattern_{i}" in ctx)
    return _result("D1-PREFS-01", count <= 8,
                    f"visible_patterns={count}/10")


def _d1_decisions_01(tmp: Path) -> dict:
    """8 decisions → at most 6 appear in context."""
    e = Engram(tmp)
    for i in range(8):
        e.add_decision(f"Decision Q{i}", choice=f"Choice {i}")
    ctx = e.generate_context()
    count = sum(1 for i in range(8) if f"Decision Q{i}" in ctx)
    return _result("D1-DECISIONS-01", count <= 6,
                    f"visible_decisions={count}/8")


def _d1_lessons_01(tmp: Path) -> dict:
    """15 distinct lessons + project → at most 8 in context."""
    e = Engram(tmp)
    # Use very different summaries to avoid dedup (SIMILARITY_THRESHOLD=0.55)
    _unique_lessons = [
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
        "Python dataclass 的 frozen 参数模拟不可变对象",
        "MCP tool description 要覆盖同义词提高召回率",
        "产品架构决策要记录 ADR 方便后人理解选择原因",
        "PostgreSQL 的 JSONB 索引用 GIN 而不是 btree",
        "SQL 查询用 EXPLAIN ANALYZE 分析执行计划优化性能",
    ]
    for summary in _unique_lessons:
        e.add_lesson(summary, domain="python")
    e.save_project_snapshot("E:/proj", {"title": "Test", "tech_stack": ["Python"]})
    lessons_stored = e.get_lessons(limit=200, _update_access=False)
    ctx = e.generate_context(project_folder="E:/proj")
    # Count how many of our test lessons appear in context
    count = sum(1 for s in _unique_lessons if s in ctx)
    ok = count <= 8 and count >= 1
    return _result("D1-LESSONS-01", ok,
                    f"visible_lessons={count}/{len(lessons_stored)}")


if __name__ == "__main__":
    import json
    result = run_d1()
    print(json.dumps(result, indent=2, ensure_ascii=False))
