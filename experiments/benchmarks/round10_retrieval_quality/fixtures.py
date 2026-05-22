"""Data factories for Round 10 retrieval quality benchmark."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from piia_engram.core import Engram, _read_json, _write_json


# ── Engram instance factories ───────────────────────────────────────


def create_full_engram(tmp: Path) -> Engram:
    """Create a fully-populated Engram for context assembly tests."""
    e = Engram(tmp)

    # Profile
    e.update_profile({
        "role": "全栈工程师",
        "description": "专注 Python 后端和 MCP 开发，5年经验",
        "technical_level": "senior",
        "language": "中文",
    })

    # Preferences
    prefs_path = e._identity_dir / "preferences.json"
    _write_json(prefs_path, {
        "work_patterns": {
            "decision_style": "数据驱动",
            "review_approach": "先看测试再看实现",
            "naming_convention": "snake_case for Python",
            "git_workflow": "feature branch + squash merge",
            "testing_strategy": "pytest + 80% coverage",
            "debugging_approach": "先复现再修",
            "documentation": "代码自文档化，只注释复杂逻辑",
            "error_handling": "fail fast + structured logging",
        },
        "communication": "简洁直接不要废话",
        "tool_preferences": {
            "editor": "VS Code",
            "terminal": "Windows Terminal",
            "ai_tool": "Claude Code",
            "test_runner": "pytest",
        },
    })

    # Quality standards
    std_path = e._identity_dir / "quality_standards.json"
    _write_json(std_path, {
        "acceptance_threshold": 4,
        "rules": [
            "必须有测试覆盖",
            "必须有中文注释（关键逻辑）",
            "不允许硬编码密钥",
            "API 变更需要向后兼容",
            "性能关键路径需要 benchmark",
        ],
    })

    # Domains
    domains_path = e._knowledge_dir / "domains.json"
    _write_json(domains_path, {
        "python": {"project_count": 12, "skills": ["FastAPI", "pytest", "asyncio"]},
        "mcp_dev": {"project_count": 5, "skills": ["FastMCP", "tool design"]},
        "frontend": {"project_count": 3, "skills": ["React", "TypeScript"]},
        "architecture": {"project_count": 8, "skills": ["microservices", "event-driven"]},
        "database": {"project_count": 4, "skills": ["PostgreSQL", "Redis"]},
        "devops": {"project_count": 2, "skills": ["Docker", "CI/CD"]},
    })

    # Lessons — 12 across domains
    _lessons = [
        ("避免在 MCP handler 里做同步阻塞 I/O", "mcp_dev"),
        ("pytest fixture scope 设为 function 避免状态泄漏", "python"),
        ("FastAPI 的 depends 注入在测试中用 override 替换", "python"),
        ("React useEffect 的 cleanup 函数必须取消异步请求", "frontend"),
        ("数据库迁移脚本必须支持回滚", "database"),
        ("Docker 多阶段构建减少镜像体积 60%", "devops"),
        ("API 版本控制用 URL prefix 而不是 header", "architecture"),
        ("产品决策要有量化指标不能拍脑袋", "产品策略"),
        ("异步任务用 asyncio.gather 并行而不是顺序 await", "python"),
        ("MCP tool description 要覆盖同义词提高召回率", "mcp_dev"),
        ("用户反馈渠道要自动化收集不能只靠人工", "产品策略"),
        ("缓存失效策略用 TTL + event-driven invalidation", "architecture"),
    ]
    for summary, domain in _lessons:
        e.add_lesson(summary, domain=domain)

    # Decisions — 8 with question/choice
    _decisions = [
        ("测试框架选型", "pytest", "生态最好，fixture 机制灵活"),
        ("API 框架选型", "FastAPI", "异步原生，自动文档"),
        ("数据库选型", "PostgreSQL", "ACID + JSON 支持"),
        ("部署方案", "Docker + Railway", "本地开发一致性"),
        ("前端框架", "React + TypeScript", "团队熟悉度最高"),
        ("知识存储格式", "JSON 文件", "无外部依赖，可 git 管理"),
        ("CI/CD 平台", "GitHub Actions", "与仓库集成最紧密"),
        ("代码风格", "black + isort + ruff", "零配置，团队统一"),
    ]
    for q, c, r in _decisions:
        e.add_decision(q, choice=c, reasoning=r)

    # Project snapshot
    e.save_project_snapshot("E:/test-project", {
        "title": "Engram Core",
        "tech_stack": ["Python", "MCP", "FastMCP"],
        "session_count": 42,
        "known_issues": [
            "domain 映射硬编码覆盖不全",
            "冲突检测尚未实现",
            "token 预算没有动态控制",
        ],
    })

    return e


def create_lessons_only(
    tmp: Path,
    lessons: list[dict[str, str]],
) -> Engram:
    """Create Engram with only specified lessons (for D3/D6 controlled tests).

    Each lesson dict: {"summary": ..., "domain": ..., "detail": ...}
    """
    e = Engram(tmp)
    for l in lessons:
        e.add_lesson(
            l["summary"],
            domain=l.get("domain", "general"),
            detail=l.get("detail", ""),
        )
    return e


def create_conflicting_decisions(tmp: Path) -> Engram:
    """Create Engram with contradictory decisions for D5 conflict tests."""
    e = Engram(tmp)
    e.add_decision(
        "Python 测试框架选型",
        choice="使用 pytest 作为唯一测试框架",
        reasoning="fixture 机制灵活，插件生态丰富",
        domain="python",
    )
    e.add_decision(
        "单元测试工具选型",
        choice="使用 unittest 标准库作为测试框架",
        reasoning="零依赖，Python 自带",
        domain="python",
    )
    return e


def patch_lesson_timestamps(
    root: Path,
    lesson_ids: list[str],
    days_ago: int,
) -> None:
    """Set last_reviewed and timestamp to N days ago for specific lessons."""
    path = root / "knowledge" / "lessons.json"
    data = _read_json(path)
    target = (datetime.now() - timedelta(days=days_ago)).isoformat()
    for entry in data:
        if entry.get("id") in lesson_ids:
            entry["last_reviewed"] = target
            entry["timestamp"] = target
    _write_json(path, data)


def get_lesson_ids(engram: Engram) -> list[str]:
    """Get all lesson IDs from an Engram instance."""
    lessons = engram.get_lessons(limit=None, _update_access=False)
    return [l["id"] for l in lessons]


# ── Test scenario data ──────────────────────────────────────────────

# 30 controlled lessons for D3/D6 recall/scoring tests
RECALL_LESSONS: list[dict[str, str]] = [
    # 10 python domain
    {"summary": "pytest 的 parametrize 装饰器支持数据驱动测试", "domain": "python", "detail": "用 @pytest.mark.parametrize 避免重复测试函数"},
    {"summary": "Python asyncio 的 TaskGroup 比 gather 更安全", "domain": "python", "detail": "TaskGroup 在异常时自动取消其他任务"},
    {"summary": "用 pathlib 替代 os.path 处理文件路径更 Pythonic", "domain": "python", "detail": "Path 对象支持 / 操作符拼接路径"},
    {"summary": "Python dataclass 的 frozen=True 可以模拟不可变对象", "domain": "python", "detail": "适合做值对象和配置类"},
    {"summary": "避免在循环中做字符串拼接，用 join 或 list append", "domain": "python", "detail": "字符串拼接 O(n^2)，join O(n)"},
    {"summary": "Python 类型标注用 X | None 替代 Optional[X]", "domain": "python", "detail": "Python 3.10+ 语法更简洁"},
    {"summary": "用 contextlib.suppress 替代空 except pass", "domain": "python", "detail": "更明确地表达意图"},
    {"summary": "Python logging 配置放在入口文件而不是库模块", "domain": "python", "detail": "避免库的 logging 配置影响使用方"},
    {"summary": "用 functools.lru_cache 做简单的内存缓存", "domain": "python", "detail": "注意 maxsize 参数避免内存泄漏"},
    {"summary": "pytest conftest.py 放在测试根目录共享 fixture", "domain": "python", "detail": "避免每个测试文件重复定义"},
    # 5 frontend domain
    {"summary": "React 组件拆分遵循单一职责原则", "domain": "frontend", "detail": "一个组件只做一件事"},
    {"summary": "TypeScript 用 interface 而不是 type 定义对象形状", "domain": "frontend", "detail": "interface 支持声明合并"},
    {"summary": "CSS-in-JS 方案用 styled-components 减少样式冲突", "domain": "frontend", "detail": "自动生成唯一 class name"},
    {"summary": "前端状态管理优先用 React Context 而不是 Redux", "domain": "frontend", "detail": "简单场景不需要 Redux 的复杂度"},
    {"summary": "用 Vite 替代 webpack 加速前端开发构建", "domain": "frontend", "detail": "ESM 原生支持，HMR 速度快 10x"},
    # 5 mcp_dev domain
    {"summary": "MCP tool 的 description 要写用户意图而不是技术实现", "domain": "mcp_dev", "detail": "让 LLM 从自然语言理解工具用途"},
    {"summary": "FastMCP 的 resource 和 tool 要区分读取和写入语义", "domain": "mcp_dev", "detail": "resource 用于无副作用查询"},
    {"summary": "MCP server 启动时做健康检查避免带病运行", "domain": "mcp_dev", "detail": "检查文件权限、配置完整性"},
    {"summary": "MCP 工具参数用 enum 限制取值范围提高准确率", "domain": "mcp_dev", "detail": "LLM 在 enum 约束下选择更准确"},
    {"summary": "MCP server 的 stdio 传输要处理好编码问题", "domain": "mcp_dev", "detail": "Windows 上 UTF-8 需要显式设置"},
    # 5 架构 (universal)
    {"summary": "微服务间通信优先用异步消息队列而不是同步 HTTP", "domain": "架构", "detail": "解耦服务依赖，提高容错"},
    {"summary": "产品架构决策要记录 ADR (Architecture Decision Record)", "domain": "架构", "detail": "后人能理解为什么当时这么选"},
    {"summary": "数据模型变更要做向后兼容的渐进式迁移", "domain": "架构", "detail": "先加新字段，再迁移数据，最后删旧字段"},
    {"summary": "系统监控的三大支柱：日志、指标、链路追踪", "domain": "架构", "detail": "三者互补，缺一不可"},
    {"summary": "核心业务逻辑不依赖框架，用六边形架构隔离", "domain": "架构", "detail": "port/adapter 模式让测试和替换更容易"},
    # 5 database domain (other)
    {"summary": "PostgreSQL 的 JSONB 索引用 GIN 而不是 btree", "domain": "database", "detail": "GIN 索引支持 @> 包含查询"},
    {"summary": "数据库连接池大小 = CPU 核数 * 2 + 磁盘数", "domain": "database", "detail": "来自 PostgreSQL 官方建议"},
    {"summary": "Redis 做缓存要设合理的 TTL 防止缓存雪崩", "domain": "database", "detail": "TTL 加随机偏移分散过期时间"},
    {"summary": "SQL 查询用 EXPLAIN ANALYZE 分析执行计划", "domain": "database", "detail": "关注 Seq Scan 和 Nested Loop"},
    {"summary": "数据库迁移脚本要有幂等性不能重复执行出错", "domain": "database", "detail": "用 IF NOT EXISTS 等守卫语句"},
]

# CJK-only lessons for D6 CJK scoring tests
CJK_LESSONS: list[dict[str, str]] = [
    {"summary": "测试框架的选择要考虑团队熟悉度", "domain": "testing", "detail": "新手多用 unittest，老手用 pytest"},
    {"summary": "记忆管理策略需要定期审查和清理", "domain": "knowledge", "detail": "过期知识要归档不要删除"},
    {"summary": "部署流程自动化减少人为错误", "domain": "devops", "detail": "CI/CD 流水线替代手动部署"},
    {"summary": "代码审查重点关注逻辑正确性而非格式", "domain": "python", "detail": "格式交给 linter 处理"},
    {"summary": "性能优化先找瓶颈再动手", "domain": "architecture", "detail": "不要过早优化"},
]
