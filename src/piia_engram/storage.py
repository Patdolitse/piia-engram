"""Engram storage layer — constants, I/O helpers, and shared utilities."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import portalocker

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "2.0"
_ENGRAM_DIR_NAME = ".engram"
_LEGACY_DIR_NAME = ".piia"
SIMILARITY_THRESHOLD = 0.55
SEARCH_RELEVANCE_THRESHOLD = 0.3   # minimum score for search results
STALE_KNOWLEDGE_DAYS = 30          # days without access before knowledge is "stale"
MAX_KNOWLEDGE_ENTRIES = 200        # cap per knowledge type (lessons / decisions)
CONFLICT_Q_THRESHOLD = 0.25   # question similarity for potential decision conflict
CONFLICT_C_CEILING = 0.80     # choice similarity ceiling — above means same choice, not conflict

# Sentiment markers for lesson conflict detection
_NEGATION_MARKERS = frozenset({
    "不", "不要", "避免", "别", "禁止", "不能", "不推荐",
    "don't", "avoid", "never", "shouldn't", "not recommended",
})
_AFFIRMATION_MARKERS = frozenset({
    "推荐", "应该", "建议", "优先", "必须",
    "recommend", "should", "prefer", "always", "must",
})

# Sensitive profile fields eligible for encryption
ENCRYPTED_PROFILE_FIELDS: set[str] = {
    "email", "phone", "location", "company",
    "real_name", "address", "id_number",
}
# Field whitelists — reject unknown keys to prevent injection of arbitrary data
_ALLOWED_PROFILE_FIELDS: frozenset = frozenset({
    "name", "role", "language", "technical_level", "description",
    "email", "phone", "location", "company", "real_name",
    "address", "id_number", "tech_stack", "years_experience",
    "specialties", "updated_at",
})
_ALLOWED_PREFERENCES_FIELDS: frozenset = frozenset({
    "work_patterns", "communication", "tool_preferences",
    "updated_at", "migrated_from",
})
_ALLOWED_TRUST_FIELDS: frozenset = frozenset({
    "default_sharing", "tool_access", "private_fields",
    "allowed_tools", "data_sharing", "restricted_fields",
    "notes", "updated_at",
})
_ALLOWED_QUALITY_FIELDS: frozenset = frozenset({
    "acceptance_threshold", "rules", "evidence_requirements",
    "review_checklist", "updated_at",
})
DEFAULT_TRUST_BOUNDARIES = {
    "default_sharing": "full",
    "tool_access": {},
    "private_fields": [],
    "allowed_tools": [],
    "data_sharing": "local_only",
    "restricted_fields": [],
    "notes": "默认所有工具可访问全部Engram数据。可按工具或字段限制。",
}
DECISION_TRIGGERS = [
    "决定", "选择", "采用", "放弃", "改用", "决策",
    "decided", "chose", "selected", "switched to", "dropped", "rejected", "went with",
]
LESSON_TRIGGERS = [
    "发现", "注意", "学到", "坑", "问题", "记住", "经验", "教训",
    "learned", "noted", "discovered", "remember", "gotcha", "caveat", "pitfall", "tip",
]
DOMAIN_KEYWORDS = {
    "python": ["python", "pip", "pytest", "django", "fastapi", "pydantic"],
    "javascript": ["js", "javascript", "node", "npm", "react", "vue", "typescript"],
    "git": ["git", "commit", "branch", "merge", "rebase", "push", "pull"],
    "docker": ["docker", "container", "image", "dockerfile", "compose"],
    "mcp": ["mcp", "tool", "server", "stdio", "model context"],
    "architecture": ["架构", "设计", "模式", "pattern", "architecture", "design"],
    "database": ["sql", "database", "query", "index", "migration", "schema"],
}
FIELD_WEIGHTS: dict[str, float] = {
    "summary": 3.0,
    "title": 3.0,
    "question": 2.5,
    "detail": 1.5,
    "choice": 1.0,
    "reasoning": 1.0,
    "domain": 0.5,
}
_TERM_ALIASES: dict[str, list[str]] = {
    "mcp": ["mcp", "model context protocol"],
    "python": ["python", "py"],
    "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"],
    "database": ["database", "db", "数据库"],
    "api": ["api", "接口"],
    "frontend": ["frontend", "前端"],
    "backend": ["backend", "后端"],
    "deploy": ["deploy", "部署"],
    "debug": ["debug", "调试"],
    "refactor": ["refactor", "重构"],
    "performance": ["performance", "性能", "优化"],
    "security": ["security", "安全"],
    "docker": ["docker", "容器"],
    "tool": ["tool", "工具"],
    "memory": ["memory", "记忆", "内存"],
    "lesson": ["lesson", "教训", "经验"],
    "decision": ["decision", "决策", "决定"],
    "search": ["search", "搜索", "查询"],
    "merge": ["merge", "合并"],
    "archive": ["archive", "归档"],
    "project": ["project", "项目"],
    "knowledge": ["knowledge", "知识"],
    "config": ["config", "配置"],
    "test": ["test", "测试"],
    "error": ["error", "错误", "报错"],
    "install": ["install", "安装"],
    "document": ["document", "文档", "doc"],
    "framework": ["framework", "框架"],
    "dependency": ["dependency", "依赖", "dep"],
}
_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in _TERM_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_LOOKUP[_alias] = _canonical


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _has_engram_data(root: Path) -> bool:
    """Return True if *root* looks like an active Engram data directory."""
    return (
        (root / "knowledge" / "lessons.json").is_file()
        or (root / "identity" / "profile.json").is_file()
    )


def _engram_root() -> Path:
    """Global Engram root directory. ENGRAM_DIR env var overrides default."""
    custom = os.environ.get("ENGRAM_DIR", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()
    home = Path.home()
    engram_root = home / _ENGRAM_DIR_NAME
    legacy_root = home / _LEGACY_DIR_NAME
    if not engram_root.exists() and legacy_root.exists():
        return legacy_root
    return engram_root


def detect_data_fragmentation(active_root: Path) -> list[str]:
    """Check all known paths for Engram data outside *active_root*.

    Returns a list of directory paths that contain data but are NOT the
    active root.  An empty list means no fragmentation detected.
    """
    candidates = [
        Path.home() / _ENGRAM_DIR_NAME,
        Path.home() / _LEGACY_DIR_NAME,
    ]
    env_dir = os.environ.get("ENGRAM_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser().resolve())

    active_resolved = active_root.resolve()
    orphans: list[str] = []
    for cand in candidates:
        try:
            if cand.resolve() == active_resolved:
                continue
            if _has_engram_data(cand):
                orphans.append(str(cand))
        except OSError:
            continue
    return orphans


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("failed to read %s: %s", path.name, exc)
        return {}


def _atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON with a file lock for concurrent writers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    lock_path = path.parent / ".engram-write.lock"

    try:
        with portalocker.Lock(lock_path, "a", timeout=5):
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = -1
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
    except portalocker.LockException as exc:
        if fd != -1:
            os.close(fd)
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(f"无法获取文件锁（超时 5s）：{path}") from exc
    except Exception:
        if fd != -1:
            os.close(fd)
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _write_json(path: Path, data: Any) -> None:
    _atomic_write_json(path, data)


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _project_id(folder: str) -> str:
    """Stable short hash for a project folder path."""
    return hashlib.sha256(folder.encode()).hexdigest()[:12]
