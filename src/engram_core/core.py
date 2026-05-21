"""Engram — AI 记忆印记，核心读写库。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import portalocker


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "2.0"
_ENGRAM_DIR_NAME = ".engram"
_LEGACY_DIR_NAME = ".piia"
SIMILARITY_THRESHOLD = 0.55

# Sensitive profile fields eligible for encryption
ENCRYPTED_PROFILE_FIELDS: set[str] = {
    "email", "phone", "location", "company",
    "real_name", "address", "id_number",
}
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
    "决定",
    "选择",
    "采用",
    "放弃",
    "改用",
    "决策",
    "decided",
    "chose",
    "selected",
    "switched to",
    "dropped",
    "rejected",
    "went with",
]
LESSON_TRIGGERS = [
    "发现",
    "注意",
    "学到",
    "坑",
    "问题",
    "记住",
    "经验",
    "教训",
    "learned",
    "noted",
    "discovered",
    "remember",
    "gotcha",
    "caveat",
    "pitfall",
    "tip",
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
}
_ALIAS_LOOKUP: dict[str, str] = {}
for _canonical, _aliases in _TERM_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_LOOKUP[_alias] = _canonical


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


# ---------------------------------------------------------------------------
# Low-level I/O
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
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


# ---------------------------------------------------------------------------
# Engram Core Class
# ---------------------------------------------------------------------------

class Engram:
    """Read/write interface to the user's global Engram."""

    def __init__(self, root: Path | None = None):
        self.root = root or _engram_root()
        self._identity_dir = self.root / "identity"
        self._knowledge_dir = self.root / "knowledge"
        self._projects_dir = self.root / "projects"
        self._exports_dir = self.root / "exports"

        # Encryption engine (transparent when ENGRAM_SECRET is not set)
        from engram_core.crypto import EncryptionEngine
        secret = os.environ.get("ENGRAM_SECRET", "").strip()
        self._crypto = EncryptionEngine(secret if secret else None)

        # Audit logger (disabled unless ENGRAM_AUDIT=1/true/yes)
        from engram_core.audit import AuditLogger
        audit_enabled = os.environ.get("ENGRAM_AUDIT", "").strip().lower() in ("1", "true", "yes")
        self._audit = AuditLogger(
            log_path=self.root / "audit.log" if audit_enabled else None,
            enabled=audit_enabled,
        )

        self._ensure_structure()

    def _ensure_structure(self) -> None:
        """Create directory structure if it doesn't exist."""
        for sub in ["identity", "knowledge", "projects", "exports", "compat"]:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        # Write schema version
        ver_path = self.root / "schema_version.json"
        if not ver_path.exists():
            _write_json(ver_path, {
                "schema_version": SCHEMA_VERSION,
                "created_at": _now_iso(),
            })
        # Auto-migrate from v1 to v2
        self._migrate_v1_to_v2()
        self._ensure_trust_boundaries()

    def _atomic_write(self, path: Path, data: Any) -> None:
        """Atomically write JSON through the shared Engram file lock."""
        _write_json(path, data)

    def _ensure_trust_boundaries(self) -> dict:
        """Backfill trust boundary defaults, including v2.2 restricted fields."""
        path = self._identity_dir / "trust_boundaries.json"
        existing = _read_json(path)
        if not isinstance(existing, dict):
            existing = {}

        changed = False
        for key, value in DEFAULT_TRUST_BOUNDARIES.items():
            if key not in existing:
                existing[key] = deepcopy(value)
                changed = True

        if changed:
            existing["updated_at"] = existing.get("updated_at") or _now_iso()
            self._atomic_write(path, existing)
        return existing

    # =====================================================================
    # Schema Migration
    # =====================================================================

    def _migrate_v1_to_v2(self) -> None:
        """Migrate from schema v1.0 to v2.0 (idempotent)."""
        ver_path = self.root / "schema_version.json"
        ver_data = _read_json(ver_path)
        current = ver_data.get("schema_version", "1.0")
        if current >= "2.0":
            return

        # 1) work_style.json → preferences.json (keep old file for compat)
        old_style = self.root / "identity" / "work_style.json"
        new_prefs = self.root / "identity" / "preferences.json"
        if old_style.is_file() and not new_prefs.is_file():
            data = _read_json(old_style)
            prefs = {
                "work_patterns": data.get("preferences", {}),
                "communication": data.get("communication", ""),
                "tool_preferences": {},
                "updated_at": _now_iso(),
                "migrated_from": "work_style.json",
            }
            _write_json(new_prefs, prefs)

        # 2) Initialize trust_boundaries.json if missing
        tb_path = self.root / "identity" / "trust_boundaries.json"
        if not tb_path.is_file():
            _write_json(tb_path, {
                "default_sharing": "full",
                "tool_access": {},
                "private_fields": [],
                "notes": "默认所有工具可访问全部Engram数据。可按工具或字段限制。",
                "updated_at": _now_iso(),
            })

        # 3) Bump schema version
        ver_data["schema_version"] = "2.0"
        ver_data["migrated_at"] = _now_iso()
        _write_json(ver_path, ver_data)

    # =====================================================================
    # Identity — who the user is
    # =====================================================================

    def get_profile(self, safe: bool = False) -> dict:
        profile = _read_json(self._identity_dir / "profile.json")
        profile = self._crypto.decrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)
        if safe:
            tb = self.get_trust_boundaries()
            restricted = set(tb.get("restricted_fields", []))
            if restricted:
                profile = {key: value for key, value in profile.items() if key not in restricted}
        self._audit.log("read", "identity/profile")
        return profile

    def get_safe_profile(self) -> dict:
        """Return profile with trust_boundaries.restricted_fields filtered out."""
        return self.get_profile(safe=True)

    def update_profile(self, updates: dict) -> None:
        """Merge updates into the user profile."""
        profile = self.get_profile()
        profile.update(updates)
        profile["updated_at"] = _now_iso()
        encrypted = self._crypto.encrypt_fields(profile, ENCRYPTED_PROFILE_FIELDS)
        _write_json(self._identity_dir / "profile.json", encrypted)
        self._audit.log("write", "identity/profile", detail=str(list(updates.keys())))

    def get_work_style(self) -> dict:
        return _read_json(self._identity_dir / "work_style.json")

    def update_work_style(self, updates: dict) -> None:
        style = self.get_work_style()
        style.update(updates)
        style["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "work_style.json", style)

    # -- Preferences (v2.0, replaces work_style) --

    def get_preferences(self) -> dict:
        """Get user preferences (v2.0). Falls back to work_style.json if needed."""
        prefs = _read_json(self._identity_dir / "preferences.json")
        if prefs:
            return prefs
        # Fallback: read old work_style.json
        old = self.get_work_style()
        if old:
            return {
                "work_patterns": old.get("preferences", {}),
                "communication": old.get("communication", ""),
                "tool_preferences": {},
            }
        return {}

    def update_preferences(self, updates: dict) -> None:
        prefs = self.get_preferences()
        prefs.update(updates)
        prefs["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "preferences.json", prefs)

    # -- Trust Boundaries (v2.0, new) --

    def get_trust_boundaries(self) -> dict:
        return self._ensure_trust_boundaries()

    def update_trust_boundaries(self, updates: dict) -> None:
        tb = self.get_trust_boundaries()
        tb.update(updates)
        tb["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "trust_boundaries.json", tb)

    def get_quality_standards(self) -> dict:
        return _read_json(self._identity_dir / "quality_standards.json")

    def update_quality_standards(self, updates: dict) -> None:
        standards = self.get_quality_standards()
        standards.update(updates)
        standards["updated_at"] = _now_iso()
        _write_json(self._identity_dir / "quality_standards.json", standards)

    # =====================================================================
    # Knowledge — what you've learned
    # =====================================================================

    def _entry_identity_text(self, entry: dict, entry_type: str) -> str:
        if entry_type == "decision":
            return str(entry.get("title") or entry.get("question") or "")
        return str(entry.get("summary") or "")

    def _ensure_fields(self, entry: dict, entry_type: str) -> dict:
        """Backfill v2.1 fields on old lesson/decision entries."""
        if not isinstance(entry, dict):
            entry = {}

        if not entry.get("timestamp"):
            entry["timestamp"] = _now_iso()
        entry.setdefault("created_at", entry.get("timestamp", _now_iso()))
        entry.setdefault("last_reviewed", entry.get("created_at", _now_iso()))

        if not entry.get("id"):
            identity = self._entry_identity_text(entry, entry_type)
            if entry_type == "lesson":
                seed = f"{identity}{entry.get('domain', '')}{entry.get('timestamp', '')}"
            else:
                seed = f"{identity}{entry.get('timestamp', '')}"
            entry["id"] = hashlib.sha256(seed.encode()).hexdigest()[:12]

        entry.setdefault("status", "active")
        entry.setdefault("access_count", 0)
        if not isinstance(entry.get("related_ids"), list):
            entry["related_ids"] = []
        return entry

    def _read_entries(self, path: Path, entry_type: str) -> list[dict]:
        entries = _read_json(path)
        if not isinstance(entries, list):
            return []

        changed = False
        ensured: list[dict] = []
        for entry in entries:
            before = dict(entry) if isinstance(entry, dict) else {}
            item = self._ensure_fields(entry, entry_type)
            if item != before:
                changed = True
            ensured.append(item)

        if changed:
            _write_json(path, ensured)
        return ensured

    def _tokenize(self, text: str, *, expand_aliases: bool = True) -> set[str]:
        """Tokenize text into character n-grams plus normalized aliases."""
        if not text:
            return set()

        text_lower = text.lower()
        tokens: set[str] = set()

        for word in re.split(r"[^a-z0-9]+", text_lower):
            if not word:
                continue
            tokens.add(word)
            canonical = _ALIAS_LOOKUP.get(word) if expand_aliases else None
            if canonical:
                tokens.add(canonical)
                tokens.update(_TERM_ALIASES.get(canonical, []))

        cjk_chars = [ch for ch in text_lower if "\u4e00" <= ch <= "\u9fff"]
        for ch in cjk_chars:
            tokens.add(ch)
            canonical = _ALIAS_LOOKUP.get(ch) if expand_aliases else None
            if canonical:
                tokens.add(canonical)
                tokens.update(_TERM_ALIASES.get(canonical, []))
        for i in range(len(cjk_chars) - 1):
            bigram = cjk_chars[i] + cjk_chars[i + 1]
            tokens.add(bigram)
            canonical = _ALIAS_LOOKUP.get(bigram) if expand_aliases else None
            if canonical:
                tokens.add(canonical)
                tokens.update(_TERM_ALIASES.get(canonical, []))

        return tokens

    def _bigram_similarity(self, a: str, b: str) -> float:
        """Similarity score using tokenized sets (works for CJK and ASCII)."""
        if not a or not b:
            return 0.0
        a_tokens = {
            token for token in self._tokenize(a, expand_aliases=False)
            if not (len(token) == 1 and "\u4e00" <= token <= "\u9fff")
        }
        b_tokens = {
            token for token in self._tokenize(b, expand_aliases=False)
            if not (len(token) == 1 and "\u4e00" <= token <= "\u9fff")
        }
        if not a_tokens or not b_tokens:
            return 0.0
        intersection = a_tokens & b_tokens
        return 2.0 * len(intersection) / (len(a_tokens) + len(b_tokens))

    def _score_item(self, item: dict, terms: list[str]) -> float:
        """Score a knowledge item against query terms using weighted fields."""
        if not terms:
            return 0.0

        query_tokens: set[str] = set()
        for term in terms:
            query_tokens.update(self._tokenize(term))
        if not query_tokens:
            return 0.0

        score = 0.0
        for field, weight in FIELD_WEIGHTS.items():
            value = str(item.get(field, "")).lower()
            if not value:
                continue
            field_tokens = self._tokenize(value)
            if not field_tokens:
                continue
            matched = len(query_tokens & field_tokens)
            score += weight * (matched / len(query_tokens))

        primary = str(
            item.get("summary")
            or item.get("title")
            or item.get("question")
            or ""
        ).lower()
        if primary:
            query_str = " ".join(terms)
            score += self._bigram_similarity(query_str, primary) * 1.5

        score += math.log1p(item.get("access_count", 0)) * 0.1
        return score

    def add_lesson(
        self,
        lesson: dict | str,
        domain: str = "",
        detail: str = "",
        source_tool: str = "",
        source_url: str = "",
        **extra: Any,
    ) -> dict:
        """Add a lesson learned.

        Accepts either the original dict form or a convenience form:
        add_lesson("summary", "domain", source_tool="codex").
        """
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")

        if isinstance(lesson, dict):
            new_lesson = dict(lesson)
        else:
            new_lesson = {"summary": str(lesson)}
            if domain:
                new_lesson["domain"] = domain
            if detail:
                new_lesson["detail"] = detail
            if source_tool:
                new_lesson["source_tool"] = source_tool
            if source_url:
                new_lesson["source_url"] = source_url

        for key, value in extra.items():
            if value is not None:
                new_lesson[key] = value

        new_lesson["timestamp"] = new_lesson.get("timestamp") or _now_iso()
        new_lesson = self._ensure_fields(new_lesson, "lesson")

        for existing in lessons:
            existing = self._ensure_fields(existing, "lesson")
            if existing.get("status") != "active":
                continue
            sim = self._bigram_similarity(
                new_lesson.get("summary", ""),
                existing.get("summary", ""),
            )
            if sim >= SIMILARITY_THRESHOLD:
                return {
                    "status": "duplicate",
                    "similarity": round(sim, 2),
                    "existing_id": existing.get("id"),
                    "existing_summary": existing.get("summary"),
                    "message": f"与现有教训相似度 {sim:.0%}，未重复添加",
                }

        lessons.append(new_lesson)
        if len(lessons) > 200:
            lessons = lessons[-200:]
        _write_json(path, lessons)

        summary = new_lesson.get("summary", "")
        self._audit.log("write", "knowledge/lessons", detail=summary[:100])
        if new_lesson.get("domain"):
            for _d in new_lesson["domain"].split(","):
                _d = _d.strip()
                if _d:
                    self.increment_domain_usage(_d)
        return new_lesson

    def get_lessons(
        self,
        domain: str | None = None,
        source_tool: str | None = None,
        limit: int | None = 50,
        _update_access: bool = True,
    ) -> list[dict]:
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")
        result = []
        for lesson in lessons:
            if lesson.get("status") != "active":
                continue
            if domain:
                lesson_domains = {d.strip() for d in (lesson.get("domain") or "").split(",") if d.strip()}
                if domain not in lesson_domains:
                    continue
            if source_tool and lesson.get("source_tool") != source_tool:
                continue
            result.append(lesson)
        result = result[-limit:] if limit is not None else result
        if _update_access and result:
            now = _now_iso()
            for lesson in result:
                lesson["last_reviewed"] = now
                lesson["access_count"] = lesson.get("access_count", 0) + 1
            _write_json(path, lessons)
        self._audit.log("read", "knowledge/lessons", detail=f"returned {len(result)} items")
        return result

    def search_knowledge(self, query: str, scope: str = "all", limit: int = 10) -> dict:
        """Search lessons and decisions by weighted multi-term relevance."""
        terms = [term for term in (query or "").lower().split() if term]
        results = {"lessons": [], "decisions": []}
        limit = max(0, int(limit))
        if not terms or limit == 0:
            return results

        if scope in ("all", "lessons"):
            path = self._knowledge_dir / "lessons.json"
            lessons = self._read_entries(path, "lesson")
            for lesson in lessons:
                if lesson.get("status") != "active":
                    continue
                score = self._score_item(lesson, terms)
                if score >= 0.3:
                    item = dict(lesson)
                    item["_score"] = round(score, 3)
                    results["lessons"].append(item)
            results["lessons"] = sorted(
                results["lessons"],
                key=lambda item: item["_score"],
                reverse=True,
            )[:limit]

        if scope in ("all", "decisions"):
            path = self._knowledge_dir / "decisions.json"
            decisions = self._read_entries(path, "decision")
            for decision in decisions:
                if decision.get("status") != "active":
                    continue
                score = self._score_item(decision, terms)
                if score >= 0.3:
                    item = dict(decision)
                    item["_score"] = round(score, 3)
                    results["decisions"].append(item)
            results["decisions"] = sorted(
                results["decisions"],
                key=lambda item: item["_score"],
                reverse=True,
            )[:limit]

        return results

    def get_relevant_lessons(self, project_folder: str | None = None,
                             limit: int = 8) -> list[dict]:
        """根据项目技术栈智能筛选教训：相关领域优先，兼顾通用教训。

        策略：
        1. 从项目快照获取 tech_stack → 映射到 domain 标签
        2. 匹配领域的教训排前面，通用/产品策略教训补充
        3. 最终按时间倒序在各组内排列

        Returns: 最多 limit 条教训（相关度排序）
        """
        all_lessons = self.get_lessons(limit=200)
        if not all_lessons:
            return []

        # 确定当前项目相关的领域
        relevant_domains: set = set()
        if project_folder:
            proj = self.get_project_snapshot(project_folder)
            tech_stack = proj.get("tech_stack", [])
            # 技术栈 → 领域映射
            stack_to_domain = {
                "python": "python", "Python": "python",
                "javascript": "frontend", "JS": "frontend",
                "HTML/CSS/JS": "frontend", "html": "frontend",
                "TypeScript": "frontend", "React": "frontend",
                "MCP": "mcp_dev", "FastMCP": "mcp_dev",
                "Claude": "claude_code", "Claude Code": "claude_code",
                "DeepSeek": "python",
            }
            for tech in tech_stack:
                domain = stack_to_domain.get(tech)
                if domain:
                    relevant_domains.add(domain)

        # 通用领域（总是相关）
        universal_domains = {"产品策略", "架构"}

        # 分桶：相关领域 / 通用 / 其他（支持多标签 domain）
        relevant = []
        universal = []
        other = []
        for lesson in reversed(all_lessons):  # 最新的在前
            lesson_domains = {d.strip() for d in (lesson.get("domain") or "").split(",") if d.strip()}
            if lesson_domains & relevant_domains:
                relevant.append(lesson)
            elif lesson_domains & universal_domains:
                universal.append(lesson)
            else:
                other.append(lesson)

        # 按比例分配: 相关领域占 60%, 通用 30%, 其他 10%
        n_relevant = max(1, int(limit * 0.6))
        n_universal = max(1, int(limit * 0.3))
        n_other = limit - n_relevant - n_universal

        result = relevant[:n_relevant] + universal[:n_universal] + other[:n_other]
        return result[:limit]

    def get_knowledge_inheritance(
        self,
        description: str,
        limit: int = 10,
    ) -> dict:
        """Return a ranked lessons + decisions inheritance pack for free text."""
        terms = [term for term in (description or "").lower().split() if term]
        limit = max(1, int(limit))

        if not terms:
            return {
                "description": description,
                "total": 0,
                "recommended_domains": [],
                "items": [],
            }

        scored: list[tuple[float, str, dict]] = []

        lessons_path = self._knowledge_dir / "lessons.json"
        for lesson in self._read_entries(lessons_path, "lesson"):
            if lesson.get("status") != "active":
                continue
            score = self._score_item(lesson, terms)
            if score > 0:
                scored.append((score, "lesson", lesson))

        decisions_path = self._knowledge_dir / "decisions.json"
        for decision in self._read_entries(decisions_path, "decision"):
            if decision.get("status") != "active":
                continue
            score = self._score_item(decision, terms)
            if score > 0:
                scored.append((score, "decision", decision))

        scored.sort(key=lambda entry: entry[0], reverse=True)
        top = scored[:limit]

        domain_counts: dict[str, int] = {}
        for _, _, item in top:
            for _d in str(item.get("domain", "")).split(","):
                _d = _d.strip()
                if _d:
                    domain_counts[_d] = domain_counts.get(_d, 0) + 1
        recommended_domains = sorted(
            domain_counts,
            key=lambda domain: (-domain_counts[domain], domain),
        )

        items = []
        for rank, (score, item_type, item) in enumerate(top, start=1):
            view = self._knowledge_view(item_type, item)
            view["rank"] = rank
            view["type"] = item_type
            view["score"] = round(score, 3)
            items.append(view)

        return {
            "description": description,
            "total": len(items),
            "recommended_domains": recommended_domains,
            "items": items,
        }

    def update_lesson(self, lesson_id: str, updates: dict) -> dict:
        """Update fields on a lesson entry."""
        path = self._knowledge_dir / "lessons.json"
        lessons = self._read_entries(path, "lesson")
        allowed_fields = {"summary", "detail", "domain", "status"}
        for lesson in lessons:
            if lesson.get("id") == lesson_id:
                for key, value in updates.items():
                    if key in allowed_fields:
                        lesson[key] = value
                lesson["last_updated"] = _now_iso()
                _write_json(path, lessons)
                return lesson
        return {"error": f"Lesson not found: {lesson_id}"}

    def archive_lesson(self, lesson_id: str) -> dict:
        """Mark a lesson as outdated without deleting it."""
        return self.update_lesson(lesson_id, {"status": "outdated"})

    def add_decision(
        self,
        decision: dict | str,
        choice: str = "",
        reasoning: str = "",
        alternatives: list[str] | None = None,
        source_tool: str = "",
        project: str = "",
        **extra: Any,
    ) -> dict:
        """Record a key decision.

        Accepts either the original dict form or:
        add_decision("question", "choice", "reasoning").
        """
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")

        if isinstance(decision, dict):
            new_decision = dict(decision)
        else:
            new_decision = {"question": str(decision), "choice": choice}
            if reasoning:
                new_decision["reasoning"] = reasoning
            if alternatives:
                new_decision["alternatives"] = alternatives
            if source_tool:
                new_decision["source_tool"] = source_tool
            if project:
                new_decision["project"] = project

        for key, value in extra.items():
            if value is not None:
                new_decision[key] = value

        new_decision["timestamp"] = new_decision.get("timestamp") or _now_iso()
        new_decision = self._ensure_fields(new_decision, "decision")

        new_title = self._entry_identity_text(new_decision, "decision")
        for existing in decisions:
            existing = self._ensure_fields(existing, "decision")
            if existing.get("status") != "active":
                continue
            sim = self._bigram_similarity(
                new_title,
                self._entry_identity_text(existing, "decision"),
            )
            if sim >= SIMILARITY_THRESHOLD:
                return {
                    "status": "duplicate",
                    "similarity": round(sim, 2),
                    "existing_id": existing.get("id"),
                    "existing_title": self._entry_identity_text(existing, "decision"),
                    "message": f"与现有决策相似度 {sim:.0%}，未重复添加",
                }

        decisions.append(new_decision)
        if len(decisions) > 200:
            decisions = decisions[-200:]
        _write_json(path, decisions)
        title = new_decision.get("question", "") or new_decision.get("title", "")
        self._audit.log("write", "knowledge/decisions", detail=title[:100])
        return new_decision

    def get_decisions(
        self,
        limit: int | None = 30,
        source_tool: str | None = None,
        project: str | None = None,
        domain: str | None = None,
        _update_access: bool = True,
    ) -> list[dict]:
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")
        result = []
        for decision in decisions:
            if decision.get("status") != "active":
                continue
            if source_tool and decision.get("source_tool") != source_tool:
                continue
            if project:
                decision_project = decision.get("project") or decision.get("source_project")
                if decision_project != project:
                    continue
            if domain:
                decision_domains = {d.strip() for d in (decision.get("domain") or "").split(",") if d.strip()}
                if domain not in decision_domains:
                    continue
            result.append(decision)
        result = result[-limit:] if limit is not None else result
        if _update_access and result:
            now = _now_iso()
            for decision in result:
                decision["last_reviewed"] = now
                decision["access_count"] = decision.get("access_count", 0) + 1
            _write_json(path, decisions)
        self._audit.log("read", "knowledge/decisions", detail=f"returned {len(result)} items")
        return result

    def update_decision(self, decision_id: str, updates: dict) -> dict:
        """Update fields on a decision entry."""
        path = self._knowledge_dir / "decisions.json"
        decisions = self._read_entries(path, "decision")
        allowed_fields = {
            "title",
            "question",
            "choice",
            "reasoning",
            "alternatives",
            "status",
            "project",
            "source_tool",
        }
        for decision in decisions:
            if decision.get("id") == decision_id:
                for key, value in updates.items():
                    if key in allowed_fields:
                        decision[key] = value
                decision["last_updated"] = _now_iso()
                _write_json(path, decisions)
                return decision
        return {"error": f"Decision not found: {decision_id}"}

    def archive_decision(self, decision_id: str) -> dict:
        """Mark a decision as outdated without deleting it."""
        return self.update_decision(decision_id, {"status": "outdated"})

    def update_knowledge(self, item_id: str, updates: dict) -> dict:
        """Update a lesson or decision by ID (auto-detects type)."""
        item_type, _ = self._find_item_by_id(item_id)
        if item_type is None:
            return {"error": f"Item not found: {item_id}"}
        if item_type == "lesson":
            return self.update_lesson(item_id, updates)
        return self.update_decision(item_id, updates)

    def archive_knowledge(self, item_id: str) -> dict:
        """Archive a lesson or decision by ID (auto-detects type)."""
        item_type, _ = self._find_item_by_id(item_id)
        if item_type is None:
            return {"error": f"Item not found: {item_id}"}
        if item_type == "lesson":
            return self.archive_lesson(item_id)
        return self.archive_decision(item_id)

    def review_knowledge(self, knowledge_id: str) -> dict:
        """Mark a lesson or decision as reviewed without changing its content."""
        lessons, decisions = self._read_link_collections()
        item_type, item = self._find_item_in_collections(knowledge_id, lessons, decisions)
        if item is None or item_type is None:
            return {"error": f"Item not found: {knowledge_id}"}

        item["last_reviewed"] = _now_iso()
        item["access_count"] = item.get("access_count", 0) + 1
        self._write_link_collections(lessons, decisions)
        self._audit.log("write", "knowledge/review", detail=knowledge_id)
        return item

    def merge_knowledge(self, primary_id: str, secondary_id: str) -> dict:
        """Merge secondary into primary, then archive the secondary item."""
        if primary_id == secondary_id:
            return {"error": "Cannot merge an item with itself"}

        lessons, decisions = self._read_link_collections()
        primary_type, primary = self._find_item_in_collections(primary_id, lessons, decisions)
        secondary_type, secondary = self._find_item_in_collections(secondary_id, lessons, decisions)

        if primary is None:
            return {"error": f"Primary item not found: {primary_id}"}
        if secondary is None:
            return {"error": f"Secondary item not found: {secondary_id}"}
        if primary.get("status") != "active":
            return {"error": f"Primary item is not active (status={primary.get('status')})"}
        if secondary.get("status") != "active":
            return {"error": f"Secondary item is not active (status={secondary.get('status')})"}

        primary_related = set(primary.get("related_ids", []))
        transferred = []
        secondary_related = list(secondary.get("related_ids", []))

        for related_id in secondary_related:
            if related_id in (primary_id, secondary_id):
                continue
            if related_id not in primary_related:
                primary_related.add(related_id)
                transferred.append(related_id)

        primary_related.discard(primary_id)
        primary_related.discard(secondary_id)
        primary["related_ids"] = sorted(primary_related)

        # Preserve bidirectional link semantics for migrated related items.
        for related_id in secondary_related:
            if related_id in (primary_id, secondary_id):
                continue
            _, related_item = self._find_item_in_collections(related_id, lessons, decisions)
            if related_item is None:
                continue
            related_ids = set(related_item.get("related_ids", []))
            related_ids.discard(secondary_id)
            related_ids.discard(related_id)
            related_ids.add(primary_id)
            related_item["related_ids"] = sorted(related_ids)

        secondary["status"] = "outdated"
        secondary["merged_into"] = primary_id
        secondary["last_updated"] = _now_iso()
        self._write_link_collections(lessons, decisions)

        return {
            "success": True,
            "primary_id": primary_id,
            "secondary_id": secondary_id,
            "secondary_archived": True,
            "related_ids_transferred": len(transferred),
            "primary_title": self._knowledge_title(primary_type, primary),
            "secondary_title": self._knowledge_title(secondary_type, secondary),
        }

    def _read_link_collections(self) -> tuple[list[dict], list[dict]]:
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        return lessons, decisions

    def _write_link_collections(self, lessons: list[dict], decisions: list[dict]) -> None:
        # Each write is individually atomic, but the two writes together are not.
        # A crash between them could leave a one-sided link. Acceptable for local single-user use.
        _write_json(self._knowledge_dir / "lessons.json", lessons)
        _write_json(self._knowledge_dir / "decisions.json", decisions)

    def _find_item_in_collections(
        self,
        item_id: str,
        lessons: list[dict],
        decisions: list[dict],
    ) -> tuple[str | None, dict | None]:
        for item in lessons:
            if item.get("id") == item_id:
                return "lesson", item
        for item in decisions:
            if item.get("id") == item_id:
                return "decision", item
        return None, None

    def _find_item_by_id(self, item_id: str) -> tuple[str | None, dict | None]:
        """Find a lesson or decision by id without updating access metadata."""
        lessons, decisions = self._read_link_collections()
        return self._find_item_in_collections(item_id, lessons, decisions)

    def _knowledge_title(self, item_type: str | None, item: dict | None) -> str:
        if not item:
            return ""
        if item_type == "decision":
            return self._entry_identity_text(item, "decision")
        return item.get("summary", "")

    def _knowledge_view(self, item_type: str, item: dict) -> dict:
        if item_type == "decision":
            return {
                "id": item.get("id", ""),
                "type": "decision",
                "title": self._entry_identity_text(item, "decision"),
                "choice": item.get("choice", ""),
                "rationale": item.get("reasoning", ""),
            }
        return {
            "id": item.get("id", ""),
            "type": "lesson",
            "title": item.get("summary", ""),
            "content": item.get("detail") or item.get("summary", ""),
            "domain": item.get("domain", ""),
        }

    def link_knowledge(self, id_a: str, id_b: str) -> dict:
        """Create a bidirectional link between two knowledge items."""
        lessons, decisions = self._read_link_collections()
        type_a, item_a = self._find_item_in_collections(id_a, lessons, decisions)
        type_b, item_b = self._find_item_in_collections(id_b, lessons, decisions)

        if item_a is None:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None:
            return {"error": f"Item not found: {id_b}"}

        if id_b not in item_a["related_ids"]:
            item_a["related_ids"].append(id_b)
        if id_a not in item_b["related_ids"]:
            item_b["related_ids"].append(id_a)
        self._write_link_collections(lessons, decisions)

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Linked: {title_a} ↔ {title_b}"}

    def unlink_knowledge(self, id_a: str, id_b: str) -> dict:
        """Remove the bidirectional link between two knowledge items."""
        lessons, decisions = self._read_link_collections()
        type_a, item_a = self._find_item_in_collections(id_a, lessons, decisions)
        type_b, item_b = self._find_item_in_collections(id_b, lessons, decisions)

        if item_a is None:
            return {"error": f"Item not found: {id_a}"}
        if item_b is None:
            return {"error": f"Item not found: {id_b}"}

        item_a["related_ids"] = [item_id for item_id in item_a["related_ids"] if item_id != id_b]
        item_b["related_ids"] = [item_id for item_id in item_b["related_ids"] if item_id != id_a]
        self._write_link_collections(lessons, decisions)

        title_a = self._knowledge_title(type_a, item_a)
        title_b = self._knowledge_title(type_b, item_b)
        return {"success": True, "message": f"Unlinked: {title_a} ↔ {title_b}"}

    def get_related_knowledge(self, item_id: str) -> dict:
        """Return all knowledge items linked to a lesson or decision id."""
        lessons, decisions = self._read_link_collections()
        item_type, item = self._find_item_in_collections(item_id, lessons, decisions)
        if item is None or item_type is None:
            return {"error": f"Item not found: {item_id}"}

        related = []
        for related_id in item.get("related_ids", []):
            related_type, related_item = self._find_item_in_collections(
                related_id,
                lessons,
                decisions,
            )
            if related_item is not None and related_type is not None:
                related.append(self._knowledge_view(related_type, related_item))

        return {
            "source": self._knowledge_view(item_type, item),
            "related": related,
            "total": len(related),
        }

    def find_similar_knowledge(self, item_id: str, limit: int = 5) -> dict:
        """Find active knowledge items with similar primary content."""
        item_type, item = self._find_item_by_id(item_id)
        if item is None or item_type is None:
            return {"error": f"Item not found: {item_id}"}

        source_text = str(
            item.get("summary")
            or item.get("title")
            or item.get("question")
            or ""
        )
        if not source_text:
            return {
                "source": self._knowledge_view(item_type, item),
                "similar": [],
                "total": 0,
            }

        candidates = []
        sources = (
            ("lesson", self._knowledge_dir / "lessons.json"),
            ("decision", self._knowledge_dir / "decisions.json"),
        )
        for entry_type, path in sources:
            entries = self._read_entries(path, entry_type)
            for entry in entries:
                if entry.get("status") != "active":
                    continue
                if entry.get("id") == item_id:
                    continue
                candidate_text = str(
                    entry.get("summary")
                    or entry.get("title")
                    or entry.get("question")
                    or ""
                )
                similarity = self._bigram_similarity(source_text, candidate_text)
                if similarity > 0.2:
                    candidate = self._knowledge_view(entry_type, entry)
                    candidate["similarity"] = round(similarity, 3)
                    candidates.append(candidate)

        candidates.sort(key=lambda candidate: candidate["similarity"], reverse=True)
        candidates = candidates[:max(0, int(limit))]
        return {
            "source": self._knowledge_view(item_type, item),
            "similar": candidates,
            "total": len(candidates),
        }

    def bulk_add_lessons(self, lessons: list, source_tool: str = "") -> dict:
        """Add multiple lessons while reusing add_lesson validation and dedupe."""
        if not isinstance(lessons, list):
            return {
                "total": 0,
                "saved": 0,
                "duplicates": 0,
                "errors": 1,
                "results": [{"status": "error", "reason": "lessons must be a list", "input": str(lessons)[:100]}],
            }
        total = len(lessons)
        saved = duplicates = errors = 0
        results = []

        for original in lessons:
            item = original
            try:
                if isinstance(item, str):
                    item = {"summary": item}
                elif isinstance(item, dict):
                    item = dict(item)
                else:
                    raise ValueError("lesson item must be a dict or string")

                summary = str(item.get("summary", "")).strip()
                if not summary:
                    raise ValueError("empty summary")
                item["summary"] = summary
                if source_tool and not item.get("source_tool"):
                    item["source_tool"] = source_tool

                result = self.add_lesson(item)
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "status": "duplicate",
                        "existing_id": result.get("existing_id"),
                        "summary": summary,
                    })
                else:
                    saved += 1
                    results.append({
                        "status": "saved",
                        "id": result.get("id"),
                        "summary": result.get("summary", summary),
                    })
            except Exception as exc:
                errors += 1
                results.append({
                    "status": "error",
                    "reason": str(exc),
                    "input": str(original)[:100],
                })

        return {
            "total": total,
            "saved": saved,
            "duplicates": duplicates,
            "errors": errors,
            "results": results,
        }

    def bulk_add_decisions(self, decisions: list, source_tool: str = "") -> dict:
        """Add multiple decisions while reusing add_decision validation and dedupe."""
        if not isinstance(decisions, list):
            return {
                "total": 0,
                "saved": 0,
                "duplicates": 0,
                "errors": 1,
                "results": [{"status": "error", "reason": "decisions must be a list", "input": str(decisions)[:100]}],
            }
        total = len(decisions)
        saved = duplicates = errors = 0
        results = []

        for original in decisions:
            item = original
            try:
                if isinstance(item, str):
                    item = {"title": item, "choice": ""}
                elif isinstance(item, dict):
                    item = dict(item)
                else:
                    raise ValueError("decision item must be a dict or string")

                title = str(item.get("title") or item.get("question") or "").strip()
                if not title:
                    raise ValueError("empty title")
                if "title" in item:
                    item["title"] = title
                else:
                    item["question"] = title
                item.setdefault("choice", "")
                if source_tool and not item.get("source_tool"):
                    item["source_tool"] = source_tool

                result = self.add_decision(item)
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "status": "duplicate",
                        "existing_id": result.get("existing_id"),
                        "title": title,
                    })
                else:
                    saved += 1
                    results.append({
                        "status": "saved",
                        "id": result.get("id"),
                        "title": self._entry_identity_text(result, "decision") or title,
                    })
            except Exception as exc:
                errors += 1
                results.append({
                    "status": "error",
                    "reason": str(exc),
                    "input": str(original)[:100],
                })

        return {
            "total": total,
            "saved": saved,
            "duplicates": duplicates,
            "errors": errors,
            "results": results,
        }

    def bulk_add_knowledge(
        self,
        items: list,
        item_type: str = "lesson",
        source_tool: str = "",
    ) -> dict:
        """Add multiple lessons or decisions in one call."""
        if item_type == "lesson":
            return self.bulk_add_lessons(items, source_tool=source_tool)
        if item_type == "decision":
            return self.bulk_add_decisions(items, source_tool=source_tool)
        return {"error": f"Unknown item_type: {item_type}. Use 'lesson' or 'decision'."}

    def _infer_domain(self, text: str, fallback: str = "") -> str:
        """Infer domain(s) from text. Returns comma-separated if multiple match."""
        text_lower = text.lower()
        matched = [
            domain
            for domain, keywords in DOMAIN_KEYWORDS.items()
            if any(kw in text_lower for kw in keywords)
        ]
        if matched:
            return ",".join(matched)
        return fallback

    def _has_content_chars(self, text: str) -> bool:
        return any(ch.isalnum() for ch in text)

    def ingest_notes(self, text: str, source_tool: str = "", domain: str = "") -> dict:
        """Parse free-form notes and extract lesson/decision candidates."""
        lines = text.splitlines()
        saved_lessons = saved_decisions = duplicates = skipped = 0
        results = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if len(line) < 5 or not self._has_content_chars(line):
                skipped += 1
                results.append({
                    "type": "unknown",
                    "status": "skipped",
                    "reason": "too short",
                    "text": line,
                })
                continue

            line_lower = line.lower()
            is_decision = any(trigger in line_lower for trigger in DECISION_TRIGGERS)
            is_lesson = any(trigger in line_lower for trigger in LESSON_TRIGGERS)
            if not is_decision and not is_lesson and len(line) <= 15:
                skipped += 1
                results.append({
                    "type": "unknown",
                    "status": "skipped",
                    "reason": "too short",
                    "text": line,
                })
                continue

            item_domain = self._infer_domain(line, domain)
            if is_decision:
                result = self.add_decision({
                    "title": line,
                    "choice": "",
                    "domain": item_domain,
                    "source_tool": source_tool,
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "decision",
                        "status": "duplicate",
                        "title": line,
                        "existing_id": result.get("existing_id"),
                        "domain": item_domain,
                    })
                else:
                    saved_decisions += 1
                    results.append({
                        "type": "decision",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "title": self._entry_identity_text(result, "decision") or line,
                        "domain": item_domain,
                    })
            else:
                result = self.add_lesson({
                    "summary": line,
                    "domain": item_domain,
                    "source_tool": source_tool,
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "lesson",
                        "status": "duplicate",
                        "summary": line,
                        "existing_id": result.get("existing_id"),
                        "domain": item_domain,
                    })
                else:
                    saved_lessons += 1
                    results.append({
                        "type": "lesson",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "summary": result.get("summary", line),
                        "domain": item_domain,
                    })

        parsed = saved_lessons + saved_decisions + duplicates
        return {
            "total_lines": len(lines),
            "parsed": parsed,
            "saved_lessons": saved_lessons,
            "saved_decisions": saved_decisions,
            "duplicates": duplicates,
            "skipped": skipped,
            "results": results,
        }

    def extract_session_insights(
        self,
        summary: str,
        source_tool: str = "",
    ) -> dict:
        """Extract lessons and decisions from a free-form session summary."""
        if not summary or not summary.strip():
            return {
                "saved_lessons": 0,
                "saved_decisions": 0,
                "duplicates": 0,
                "skipped": 0,
                "results": [],
            }

        sentences = re.split(r"[。！？.!?\n]+", summary)
        saved_lessons = saved_decisions = duplicates = skipped = 0
        results = []

        for raw in sentences:
            sentence = raw.strip()
            if not sentence or len(sentence) < 8:
                skipped += 1
                continue
            if not self._has_content_chars(sentence):
                skipped += 1
                continue

            sentence_lower = sentence.lower()
            is_decision = any(trigger in sentence_lower for trigger in DECISION_TRIGGERS)
            is_lesson = any(trigger in sentence_lower for trigger in LESSON_TRIGGERS)

            if not is_decision and not is_lesson:
                if re.search(
                    r"(应该|需要|必须|建议|最好|注意|避免|不要|要|should|must|need|avoid|remember|make sure)",
                    sentence_lower,
                ):
                    is_lesson = True
                elif re.search(
                    r"(因此|所以|最终|改为|使用|采用|选择了|therefore|so we|decided to|chose|switched)",
                    sentence_lower,
                ):
                    is_decision = True

            if not is_decision and not is_lesson:
                skipped += 1
                results.append({"status": "skipped", "text": sentence[:80]})
                continue

            item_domain = self._infer_domain(sentence)
            if is_decision:
                result = self.add_decision({
                    "title": sentence,
                    "choice": "",
                    "domain": item_domain,
                    "source_tool": source_tool,
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "decision",
                        "status": "duplicate",
                        "title": sentence[:80],
                        "existing_id": result.get("existing_id"),
                    })
                else:
                    saved_decisions += 1
                    results.append({
                        "type": "decision",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "title": sentence[:80],
                        "domain": item_domain,
                    })
            else:
                result = self.add_lesson({
                    "summary": sentence,
                    "domain": item_domain,
                    "source_tool": source_tool,
                })
                if result.get("status") == "duplicate":
                    duplicates += 1
                    results.append({
                        "type": "lesson",
                        "status": "duplicate",
                        "summary": sentence[:80],
                        "existing_id": result.get("existing_id"),
                    })
                else:
                    saved_lessons += 1
                    results.append({
                        "type": "lesson",
                        "status": "saved",
                        "id": result.get("id", ""),
                        "summary": sentence[:80],
                        "domain": item_domain,
                    })

        return {
            "saved_lessons": saved_lessons,
            "saved_decisions": saved_decisions,
            "duplicates": duplicates,
            "skipped": skipped,
            "results": results,
        }

    def update_domain(self, domain: str, updates: dict) -> None:
        """Update skill/experience data for a domain (e.g. "python", "frontend")."""
        path = self._knowledge_dir / "domains.json"
        domains = _read_json(path)
        if not isinstance(domains, dict):
            domains = {}
        if domain not in domains:
            domains[domain] = {"first_seen": _now_iso(), "project_count": 0}
        domains[domain].update(updates)
        domains[domain]["updated_at"] = _now_iso()
        _write_json(path, domains)

    def get_domains(self) -> dict:
        path = self._knowledge_dir / "domains.json"
        stored = _read_json(path)
        if not isinstance(stored, dict):
            stored = {}

        active_counts: dict[str, int] = {}
        for lesson in self.get_lessons(limit=None, _update_access=False):
            raw = lesson.get("domain") or ""
            for _d in raw.split(","):
                _d = _d.strip()
                if _d:
                    active_counts[_d] = active_counts.get(_d, 0) + 1

        result: dict[str, dict] = {}
        for domain, count in active_counts.items():
            entry = stored.get(domain, {})
            if not isinstance(entry, dict):
                entry = {}
            merged = dict(entry)
            merged["project_count"] = count
            result[domain] = merged
        return result

    def increment_domain_usage(self, domain: str) -> None:
        """Increment project count for a domain."""
        path = self._knowledge_dir / "domains.json"
        domains = _read_json(path)
        if not isinstance(domains, dict):
            domains = {}
        entry = domains.get(domain, {"first_seen": _now_iso(), "project_count": 0})
        entry["project_count"] = entry.get("project_count", 0) + 1
        entry["last_used"] = _now_iso()
        domains[domain] = entry
        _write_json(path, domains)

    # =====================================================================
    # Projects — per-project knowledge
    # =====================================================================

    def save_project_snapshot(self, project_folder: str, data: dict) -> None:
        """Save/update knowledge for a specific project."""
        pid = _project_id(project_folder)
        path = self._projects_dir / f"{pid}.json"
        existing = _read_json(path)
        existing.update(data)
        existing["project_folder"] = project_folder
        existing["updated_at"] = _now_iso()
        if "created_at" not in existing:
            existing["created_at"] = _now_iso()
        _write_json(path, existing)

    def get_project_snapshot(self, project_folder: str) -> dict:
        pid = _project_id(project_folder)
        return _read_json(self._projects_dir / f"{pid}.json")

    def list_projects(self) -> list[dict]:
        """List all known projects with basic info."""
        result = []
        for f in sorted(self._projects_dir.glob("*.json")):
            data = _read_json(f)
            if data:
                result.append({
                    "id": f.stem,
                    "folder": data.get("project_folder", ""),
                    "title": data.get("title", ""),
                    "updated_at": data.get("updated_at", ""),
                    "session_count": data.get("session_count", 0),
                })
        return result

    # =====================================================================
    # Smart Cold-Start — generate context for a new AI session
    # =====================================================================

    def generate_context(self, project_folder: str | None = None) -> str:
        """Generate a concise context block that any AI can consume.

        This is the magic moment — inject this into any AI's system prompt
        and it immediately "knows" you.
        """
        lines: list[str] = []

        # Identity
        profile = self.get_safe_profile()
        if profile:
            lines.append("## 关于用户")
            if profile.get("role"):
                lines.append(f"- 角色: {profile['role']}")
            if profile.get("language"):
                lines.append(f"- 沟通语言: {profile['language']}")
            if profile.get("technical_level"):
                lines.append(f"- 技术水平: {profile['technical_level']}")
            if profile.get("description"):
                lines.append(f"- 简介: {profile['description']}")

        # Preferences (v2.0) — falls back to work_style for v1
        prefs = self.get_preferences()
        if prefs:
            lines.append("\n## 工作偏好")
            if prefs.get("work_patterns"):
                for k, v in list(prefs["work_patterns"].items())[:8]:
                    lines.append(f"- {k}: {v}")
            if prefs.get("communication"):
                lines.append(f"- 沟通偏好: {prefs['communication']}")
            if prefs.get("tool_preferences"):
                lines.append("- 工具偏好:")
                for k, v in list(prefs["tool_preferences"].items())[:4]:
                    lines.append(f"  - {k}: {v}")

        # Quality standards
        standards = self.get_quality_standards()
        if standards:
            lines.append("\n## 质量标准")
            if standards.get("acceptance_threshold"):
                lines.append(f"- 验收严格度: {standards['acceptance_threshold']}/5")
            if standards.get("rules"):
                for rule in standards["rules"][:5]:
                    lines.append(f"- {rule}")

        # Domain experience
        domains = self.get_domains()
        if domains:
            lines.append("\n## 经验领域")
            sorted_domains = sorted(
                domains.items(),
                key=lambda x: x[1].get("project_count", 0),
                reverse=True,
            )
            for name, info in sorted_domains[:6]:
                count = info.get("project_count", 0)
                lines.append(f"- {name}: {count} 个项目经验")

        # Smart lessons: domain-relevant + recent universal
        lessons = self.get_relevant_lessons(
            project_folder=project_folder, limit=8
        )
        if lessons:
            lines.append("\n## 相关经验教训（请在开发中主动避免）")
            for l in lessons:
                lines.append(f"- {l.get('summary', '')}")

        # Recent key decisions (so AI doesn't re-litigate settled choices)
        decisions = self.get_decisions(limit=6, _update_access=False)
        if decisions:
            lines.append("\n## 已做的关键决策（请遵循）")
            for d in decisions:
                question = d.get("question") or d.get("title") or ""
                choice = d.get("choice", "")
                if question and choice:
                    lines.append(f"- {question} → {choice}")
                elif question:
                    lines.append(f"- {question}")
                elif choice:
                    lines.append(f"- {choice}")

        # Project-specific context
        if project_folder:
            proj = self.get_project_snapshot(project_folder)
            if proj:
                lines.append(f"\n## 当前项目历史")
                if proj.get("title"):
                    lines.append(f"- 项目: {proj['title']}")
                if proj.get("session_count"):
                    lines.append(f"- 已协作 {proj['session_count']} 次")
                if proj.get("tech_stack"):
                    lines.append(f"- 技术栈: {', '.join(proj['tech_stack'])}")
                if proj.get("known_issues"):
                    lines.append("- 已知问题:")
                    for issue in proj["known_issues"][:3]:
                        lines.append(f"  - {issue}")

        stale = self.get_stale_knowledge(days=30, limit=None)
        stale_count = len(stale["lessons"]) + len(stale["decisions"])
        if stale_count > 5:
            lines.append("\n## stale_knowledge_warning")
            lines.append(f"- 有 {stale_count} 条知识超过 30 天未复习，建议运行 get_stale_knowledge 查看。")

        if not lines:
            return ""

        return "\n".join(lines)

    # =====================================================================
    # Identity Card Export — portable summary for any AI tool
    # =====================================================================

    def export_identity_card(self) -> str:
        """Export a portable Markdown identity card.

        User can paste this into ANY AI tool (Claude, GPT, Cursor, etc.)
        and the AI will immediately understand their work style.
        """
        lines = [
            "# 我的 AI 协作身份卡",
            f"_生成时间: {_now_iso()}_",
            f"_由 Engram 自动生成_",
            "",
        ]

        profile = self.get_profile()
        if profile:
            lines.append("## 我是谁")
            if profile.get("role"):
                lines.append(f"- {profile['role']}")
            if profile.get("description"):
                lines.append(f"- {profile['description']}")
            if profile.get("language"):
                lines.append(f"- 使用语言: {profile['language']}")
            if profile.get("technical_level"):
                lines.append(f"- 技术水平: {profile['technical_level']}")
            lines.append("")

        style = self.get_work_style()
        if style:
            lines.append("## 我的工作方式")
            if style.get("preferences"):
                for k, v in style["preferences"].items():
                    lines.append(f"- {k}: {v}")
            if style.get("communication"):
                lines.append(f"- {style['communication']}")
            lines.append("")

        standards = self.get_quality_standards()
        if standards:
            lines.append("## 我的质量标准")
            if standards.get("rules"):
                for rule in standards["rules"]:
                    lines.append(f"- {rule}")
            lines.append("")

        domains = self.get_domains()
        if domains:
            lines.append("## 我的经验")
            for name, info in sorted(
                domains.items(),
                key=lambda x: x[1].get("project_count", 0),
                reverse=True,
            ):
                count = info.get("project_count", 0)
                lines.append(f"- {name} ({count} 个项目)")
            lines.append("")

        lessons = self.get_lessons(limit=10)
        if lessons:
            lines.append("## 我踩过的坑（请帮我避免）")
            for l in lessons:
                lines.append(f"- {l.get('summary', '')}")
            lines.append("")

        decisions = self.get_decisions(limit=8, _update_access=False)
        if decisions:
            lines.append("## 我的关键决策（请遵循）")
            for d in decisions:
                question = d.get("question") or d.get("title") or ""
                choice = d.get("choice", "")
                if question and choice:
                    lines.append(f"- {question} → {choice}")
                elif question:
                    lines.append(f"- {question}")
                elif choice:
                    lines.append(f"- {choice}")
            lines.append("")

        lines.append("---")
        lines.append("_把这段文字粘贴到任何 AI 对话的开头，AI 就能立刻了解你。_")

        card = "\n".join(lines)

        # Also save to exports folder
        export_path = self._exports_dir / "identity_card.md"
        export_path.write_text(card, encoding="utf-8")

        return card

    # =====================================================================
    # Stats — user's knowledge asset metrics
    # =====================================================================

    def get_health_report(self) -> dict:
        """Generate a health report for the knowledge asset."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")

        active_lessons = [l for l in lessons if l.get("status") == "active"]
        outdated_lessons = [l for l in lessons if l.get("status") == "outdated"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]
        outdated_decisions = [d for d in decisions if d.get("status") == "outdated"]

        domain_counts: dict[str, int] = {}
        for lesson in active_lessons:
            raw = lesson.get("domain") or "unknown"
            for _d in raw.split(","):
                _d = _d.strip() or "unknown"
                domain_counts[_d] = domain_counts.get(_d, 0) + 1

        source_counts: dict[str, int] = {}
        for item in active_lessons + active_decisions:
            source = item.get("source_tool", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        duplicates = []
        for i, first in enumerate(active_lessons):
            for second in active_lessons[i + 1:]:
                first_summary = first.get("summary", "")
                second_summary = second.get("summary", "")
                if min(len(first_summary), len(second_summary)) < 8:
                    continue
                sim = self._bigram_similarity(first_summary, second_summary)
                if sim >= 0.45:
                    duplicates.append({
                        "a_id": first.get("id"),
                        "a_summary": first_summary[:50],
                        "b_id": second.get("id"),
                        "b_summary": second_summary[:50],
                        "similarity": round(sim, 2),
                    })

        warnings = []
        if len(active_lessons) > 150:
            warnings.append(f"教训数量较多（{len(active_lessons)}/200），建议清理过时条目")
        if duplicates:
            warnings.append(f"发现 {len(duplicates)} 对近似重复教训，建议合并")
        if outdated_lessons:
            warnings.append(f"{len(outdated_lessons)} 条教训已标记过时，可考虑清理")

        review_cutoff = datetime.now() - timedelta(days=30)
        archive_cutoff = datetime.now() - timedelta(days=60)
        lifecycle_items = [
            ("lesson", item)
            for item in active_lessons
        ] + [
            ("decision", item)
            for item in active_decisions
        ]
        items_needing_review = []
        items_to_archive = []
        for item_type, item in lifecycle_items:
            reviewed_at = self._reviewed_at(item)
            if not reviewed_at:
                continue
            view = self._lifecycle_review_view(item_type, item)
            if item.get("access_count", 0) >= 3 and reviewed_at <= review_cutoff:
                items_needing_review.append(view)
            if item.get("access_count", 0) == 0 and reviewed_at <= archive_cutoff:
                items_to_archive.append(view)

        items_needing_review.sort(key=lambda item: item.get("last_reviewed", ""))
        items_to_archive.sort(key=lambda item: item.get("last_reviewed", ""))

        return {
            "summary": {
                "total_lessons": len(lessons),
                "active_lessons": len(active_lessons),
                "outdated_lessons": len(outdated_lessons),
                "total_decisions": len(decisions),
                "active_decisions": len(active_decisions),
                "outdated_decisions": len(outdated_decisions),
            },
            "domain_distribution": domain_counts,
            "source_distribution": source_counts,
            "potential_duplicates": duplicates[:10],
            "items_needing_review": items_needing_review[:10],
            "items_to_archive": items_to_archive[:10],
            "warnings": warnings,
        }

    def _reviewed_at(self, item: dict) -> datetime | None:
        return (
            _parse_iso(item.get("last_reviewed"))
            or _parse_iso(item.get("created_at"))
            or _parse_iso(item.get("timestamp"))
        )

    def _lifecycle_review_view(self, item_type: str, item: dict) -> dict:
        return {
            "id": item.get("id", ""),
            "type": item_type,
            "title": self._knowledge_title(item_type, item),
            "last_reviewed": item.get("last_reviewed") or item.get("created_at", ""),
            "access_count": item.get("access_count", 0),
        }

    def get_stale_knowledge(self, days: int = 30, limit: int | None = 20) -> dict:
        """Return active lessons and decisions not reviewed for more than days."""
        days = max(0, int(days))
        cutoff = datetime.now() - timedelta(days=days)
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")

        stale_lessons = []
        for lesson in lessons:
            if lesson.get("status") != "active":
                continue
            reviewed_at = self._reviewed_at(lesson)
            if reviewed_at and reviewed_at <= cutoff:
                stale_lessons.append({
                    "id": lesson.get("id", ""),
                    "type": "lesson",
                    "title": lesson.get("summary", ""),
                    "domain": lesson.get("domain", ""),
                    "last_reviewed": lesson.get("last_reviewed") or lesson.get("created_at", ""),
                    "access_count": lesson.get("access_count", 0),
                })

        stale_decisions = []
        for decision in decisions:
            if decision.get("status") != "active":
                continue
            reviewed_at = self._reviewed_at(decision)
            if reviewed_at and reviewed_at <= cutoff:
                stale_decisions.append({
                    "id": decision.get("id", ""),
                    "type": "decision",
                    "title": self._entry_identity_text(decision, "decision"),
                    "domain": decision.get("domain") or decision.get("project", ""),
                    "last_reviewed": decision.get("last_reviewed") or decision.get("created_at", ""),
                    "access_count": decision.get("access_count", 0),
                })

        stale_items = stale_lessons + stale_decisions
        stale_items.sort(key=lambda item: item.get("last_reviewed", ""))
        if limit is not None:
            limit = max(0, int(limit))
            stale_items = stale_items[:limit]
        stale_lessons = [item for item in stale_items if item.get("type") == "lesson"]
        stale_decisions = [item for item in stale_items if item.get("type") == "decision"]

        return {
            "days": days,
            "limit": limit,
            "lessons": stale_lessons,
            "decisions": stale_decisions,
        }

    def get_knowledge_digest(self) -> dict:
        """Return aggregate knowledge summary without updating access metadata."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        active_lessons = [l for l in lessons if l.get("status") == "active"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]

        recent_cutoff = datetime.now() - timedelta(days=7)
        recent_items = []
        for lesson in active_lessons:
            created_at = lesson.get("created_at") or lesson.get("timestamp", "")
            created_dt = _parse_iso(created_at)
            if created_dt and created_dt >= recent_cutoff:
                recent_items.append({
                    "type": "lesson",
                    "title": lesson.get("summary", ""),
                    "domain": lesson.get("domain", ""),
                    "created_at": created_at,
                })
        for decision in active_decisions:
            created_at = decision.get("created_at") or decision.get("timestamp", "")
            created_dt = _parse_iso(created_at)
            if created_dt and created_dt >= recent_cutoff:
                recent_items.append({
                    "type": "decision",
                    "title": self._entry_identity_text(decision, "decision"),
                    "domain": decision.get("domain") or decision.get("project", ""),
                    "created_at": created_at,
                })
        recent_items.sort(key=lambda item: item.get("created_at", ""), reverse=True)

        by_domain: dict[str, dict[str, int]] = {}
        for lesson in active_lessons:
            raw = lesson.get("domain") or "unknown"
            for _d in raw.split(","):
                _d = _d.strip() or "unknown"
                bucket = by_domain.setdefault(_d, {"lessons": 0, "decisions": 0})
                bucket["lessons"] += 1
        for decision in active_decisions:
            domain = (
                decision.get("domain")
                or decision.get("project")
                or decision.get("source_project")
                or "unknown"
            )
            bucket = by_domain.setdefault(domain, {"lessons": 0, "decisions": 0})
            bucket["decisions"] += 1

        top_lessons = sorted(
            active_lessons,
            key=lambda item: item.get("access_count", 0),
            reverse=True,
        )[:5]
        top_decisions = sorted(
            active_decisions,
            key=lambda item: item.get("access_count", 0),
            reverse=True,
        )[:5]
        stale = self.get_stale_knowledge(days=30)

        return {
            "total_lessons": len(active_lessons),
            "total_decisions": len(active_decisions),
            "recent_additions": {
                "last_7_days": len(recent_items),
                "items": recent_items[:10],
            },
            "top_accessed": {
                "lessons": [
                    {
                        "title": item.get("summary", ""),
                        "access_count": item.get("access_count", 0),
                        "domain": item.get("domain", ""),
                    }
                    for item in top_lessons
                ],
                "decisions": [
                    {
                        "title": self._entry_identity_text(item, "decision"),
                        "access_count": item.get("access_count", 0),
                    }
                    for item in top_decisions
                ],
            },
            "by_domain": by_domain,
            "stale_count": len(stale["lessons"]) + len(stale["decisions"]),
        }

    def get_knowledge_overview(self, section: str = "all", stale_days: int = 30) -> dict:
        """Unified overview combining digest, health, and stale checks."""
        result: dict = {}
        if section in ("all", "digest"):
            result["digest"] = self.get_knowledge_digest()
        if section in ("all", "health"):
            result["health"] = self.get_health_report()
        if section in ("all", "stale"):
            result["stale"] = self.get_stale_knowledge(days=stale_days)
        if not result:
            return {"error": f"Unknown section: {section}. Use: all, digest, health, stale."}
        return result

    def export_knowledge_report(self) -> str:
        """Generate and save a Chinese Markdown knowledge report."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        active_lessons = [l for l in lessons if l.get("status") == "active"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]
        domains = sorted({l.get("domain") for l in active_lessons if l.get("domain")})
        stale = self.get_stale_knowledge(days=30)
        title_by_id = {
            **{lesson.get("id", ""): lesson.get("summary", "") for lesson in lessons},
            **{
                decision.get("id", ""): self._entry_identity_text(decision, "decision")
                for decision in decisions
            },
        }

        def related_title_line(item: dict) -> str:
            titles = [
                title_by_id[item_id]
                for item_id in item.get("related_ids", [])
                if title_by_id.get(item_id)
            ]
            return f"  *关联：{', '.join(titles)}*" if titles else ""

        lines = [
            "# 个人知识报告",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 概览",
            f"- 经验教训：{len(active_lessons)} 条（活跃）",
            f"- 关键决策：{len(active_decisions)} 条（活跃）",
            f"- 覆盖领域：{', '.join(domains) if domains else '暂无'}",
            "",
            "## 经验教训",
            "",
        ]

        lessons_by_domain: dict[str, list[dict]] = {}
        for lesson in active_lessons:
            raw = lesson.get("domain") or "未分类"
            domains = [d.strip() for d in raw.split(",") if d.strip()] or ["未分类"]
            for _d in domains:
                lessons_by_domain.setdefault(_d, []).append(lesson)
        if lessons_by_domain:
            for domain in sorted(lessons_by_domain):
                lines.append(f"### {domain}")
                for lesson in lessons_by_domain[domain]:
                    source = lesson.get("source_tool", "unknown")
                    access_count = lesson.get("access_count", 0)
                    summary = lesson.get("summary", "")
                    detail = lesson.get("detail", "")
                    body = f" — {detail}" if detail else ""
                    lines.append(
                        f"- **{summary}**{body} *(来源: {source}, 访问 {access_count} 次)*"
                    )
                    related_line = related_title_line(lesson)
                    if related_line:
                        lines.append(related_line)
                lines.append("")
        else:
            lines.extend(["暂无经验教训。", ""])

        lines.extend(["## 关键决策", ""])
        decisions_by_month: dict[str, list[dict]] = {}
        for decision in active_decisions:
            created = decision.get("created_at") or decision.get("timestamp", "")
            month = created[:7] if len(created) >= 7 else "未知时间"
            decisions_by_month.setdefault(month, []).append(decision)
        if decisions_by_month:
            for month in sorted(decisions_by_month, reverse=True):
                lines.append(f"### {month}")
                for decision in decisions_by_month[month]:
                    title = self._entry_identity_text(decision, "decision")
                    rationale = decision.get("reasoning") or decision.get("choice", "")
                    status = decision.get("status", "active")
                    lines.append(f"- **{title}** — {rationale} *(状态: {status})*")
                    related_line = related_title_line(decision)
                    if related_line:
                        lines.append(related_line)
                lines.append("")
        else:
            lines.extend(["暂无关键决策。", ""])

        lines.extend(["## 待复查（超过 30 天未访问）", ""])
        stale_items = stale["lessons"] + stale["decisions"]
        if stale_items:
            for item in stale_items:
                lines.append(
                    f"- **{item.get('title', '')}** — 最后访问: {item.get('last_reviewed', '')}"
                )
        else:
            lines.append("暂无超过 30 天未访问的活跃知识。")
        lines.append("")

        report = "\n".join(lines)
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self._exports_dir / f"knowledge_report_{date_str}.md"
        counter = 1
        while report_path.exists():
            report_path = self._exports_dir / f"knowledge_report_{date_str}_{counter}.md"
            counter += 1
        report_path.write_text(report, encoding="utf-8")
        return report

    def get_stats(self) -> dict:
        """Return statistics about the user's knowledge asset."""
        lessons = self._read_entries(self._knowledge_dir / "lessons.json", "lesson")
        decisions = self._read_entries(self._knowledge_dir / "decisions.json", "decision")
        active_lessons = [l for l in lessons if l.get("status") == "active"]
        active_decisions = [d for d in decisions if d.get("status") == "active"]
        domains = self.get_domains()
        projects = self.list_projects()
        profile = self.get_profile()

        source_breakdown = {}
        for lesson in active_lessons:
            tool = lesson.get("source_tool", "unknown")
            source_breakdown[tool] = source_breakdown.get(tool, 0) + 1

        return {
            "total_lessons": len(lessons),
            "active_lessons": len(active_lessons),
            "outdated_lessons": len(lessons) - len(active_lessons),
            "total_decisions": len(decisions),
            "active_decisions": len(active_decisions),
            "outdated_decisions": len(decisions) - len(active_decisions),
            "domain_count": len(domains),
            "project_count": len(projects),
            "domains": {
                name: info.get("project_count", 0)
                for name, info in domains.items()
            },
            "lessons_by_source_tool": source_breakdown,
            "has_profile": bool(profile),
            "has_preferences": bool(self.get_preferences()),
            "has_quality_standards": bool(self.get_quality_standards()),
            "has_trust_boundaries": bool(self.get_trust_boundaries()),
            "schema_version": SCHEMA_VERSION,
        }

    # =====================================================================
    # Import / Export — 备份、迁移、跨机器同步
    # =====================================================================

    def export_all(self, output_path: str | None = None) -> str:
        """导出整个 Engram 为单一 JSON 文件。

        包含：identity、knowledge、projects 所有数据。
        用于备份或迁移到另一台机器。

        Args:
            output_path: 导出文件路径。默认存到 ~/.engram/exports/engram_backup_<date>.json

        Returns:
            导出文件的完整路径。
        """
        export_data = {
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "identity": {
                "profile": self.get_profile(),
                "preferences": self.get_preferences(),
                "work_style": self.get_work_style(),  # backward compat
                "quality_standards": self.get_quality_standards(),
                "trust_boundaries": self.get_trust_boundaries(),
            },
            "knowledge": {
                "lessons": _read_json(self._knowledge_dir / "lessons.json") or [],
                "decisions": _read_json(self._knowledge_dir / "decisions.json") or [],
                "domains": self.get_domains(),
            },
            "projects": {},
        }

        # 导出所有项目快照
        for f in sorted(self._projects_dir.glob("*.json")):
            data = _read_json(f)
            if data:
                export_data["projects"][f.stem] = data

        # 确定输出路径
        if output_path:
            out = Path(output_path)
        else:
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = self._exports_dir / f"engram_backup_{date_str}.json"

        out.parent.mkdir(parents=True, exist_ok=True)
        _write_json(out, export_data)
        self._audit.log("export", "all", detail=f"exported to {out}")
        return str(out)

    def import_all(self, input_path: str, merge: bool = True) -> dict:
        """从备份文件导入 Engram 数据。

        Args:
            input_path: 备份文件路径（export_all 生成的 JSON）。
            merge: True=合并（已有数据保留，新数据追加），False=覆盖。

        Returns:
            导入结果摘要。
        """
        path = Path(input_path)
        if not path.is_file():
            return {"error": f"文件不存在: {input_path}"}

        data = _read_json(path)
        if not data or "schema_version" not in data:
            return {"error": "不是有效的 Engram 备份文件"}

        imported = []

        # Identity
        identity = data.get("identity", {})
        if identity.get("profile"):
            if merge:
                existing = self.get_profile()
                # 合并：新字段补充，不覆盖已有值
                for k, v in identity["profile"].items():
                    if k not in existing or not existing[k]:
                        existing[k] = v
                self.update_profile(existing)
            else:
                _write_json(self._identity_dir / "profile.json", identity["profile"])
            imported.append("profile")

        if identity.get("work_style"):
            if merge:
                existing = self.get_work_style()
                existing.update(identity["work_style"])
                self.update_work_style(existing)
            else:
                _write_json(self._identity_dir / "work_style.json", identity["work_style"])
            imported.append("work_style")

        if identity.get("quality_standards"):
            if merge:
                existing = self.get_quality_standards()
                new_rules = identity["quality_standards"].get("rules", [])
                old_rules = set(existing.get("rules", []))
                merged_rules = list(old_rules | set(new_rules))
                existing["rules"] = merged_rules[-15:]
                if identity["quality_standards"].get("acceptance_threshold"):
                    existing["acceptance_threshold"] = identity["quality_standards"]["acceptance_threshold"]
                self.update_quality_standards(existing)
            else:
                _write_json(self._identity_dir / "quality_standards.json", identity["quality_standards"])
            imported.append("quality_standards")

        # Knowledge
        knowledge = data.get("knowledge", {})

        if knowledge.get("lessons"):
            if merge:
                existing = _read_json(self._knowledge_dir / "lessons.json") or []
                existing_summaries = {l.get("summary", "") for l in existing}
                new_count = 0
                for lesson in knowledge["lessons"]:
                    if lesson.get("summary") not in existing_summaries:
                        existing.append(lesson)
                        existing_summaries.add(lesson.get("summary", ""))
                        new_count += 1
                # Keep last 200
                _write_json(self._knowledge_dir / "lessons.json", existing[-200:])
                imported.append(f"lessons(+{new_count})")
            else:
                _write_json(self._knowledge_dir / "lessons.json", knowledge["lessons"][-200:])
                imported.append(f"lessons({len(knowledge['lessons'])})")

        if knowledge.get("decisions"):
            if merge:
                existing = _read_json(self._knowledge_dir / "decisions.json") or []
                existing_questions = {d.get("question", "") for d in existing}
                new_count = 0
                for decision in knowledge["decisions"]:
                    if decision.get("question") not in existing_questions:
                        existing.append(decision)
                        existing_questions.add(decision.get("question", ""))
                        new_count += 1
                _write_json(self._knowledge_dir / "decisions.json", existing[-200:])
                imported.append(f"decisions(+{new_count})")
            else:
                _write_json(self._knowledge_dir / "decisions.json", knowledge["decisions"][-200:])
                imported.append(f"decisions({len(knowledge['decisions'])})")

        if knowledge.get("domains"):
            if merge:
                existing = self.get_domains()
                for name, info in knowledge["domains"].items():
                    if name not in existing:
                        existing[name] = info
                    else:
                        # 取更大的 project_count
                        existing[name]["project_count"] = max(
                            existing[name].get("project_count", 0),
                            info.get("project_count", 0),
                        )
                _write_json(self._knowledge_dir / "domains.json", existing)
            else:
                _write_json(self._knowledge_dir / "domains.json", knowledge["domains"])
            imported.append("domains")

        # Projects
        projects = data.get("projects", {})
        if projects:
            for pid, proj_data in projects.items():
                proj_path = self._projects_dir / f"{pid}.json"
                if merge and proj_path.exists():
                    existing = _read_json(proj_path)
                    existing.update(proj_data)
                    _write_json(proj_path, existing)
                else:
                    _write_json(proj_path, proj_data)
            imported.append(f"projects({len(projects)})")

        self._audit.log("import", "all", detail=f"imported from {input_path}")
        return {
            "status": "success",
            "mode": "merge" if merge else "overwrite",
            "imported": imported,
            "source": input_path,
        }


# ---------------------------------------------------------------------------
# Knowledge Extraction Engine
# ---------------------------------------------------------------------------

# Prompt for extracting knowledge from a conversation

EXTRACTION_PROMPT = """分析以下 AI 协作对话，从中提取用户的个人知识。

## 对话内容
{conversation}

## 项目信息
- 项目文件夹: {project_folder}
- 项目文件: {project_files}

## 要提取的内容

请从对话中识别以下信息（只提取能观察到的，不要推测）：

1. **用户偏好** — 用户表达了哪些偏好？（技术选择、方案倾向、沟通方式等）
2. **质量标准** — 用户对结果有什么要求？（验收标准、修改要求、不满意的点）
3. **教训** — 过程中遇到了什么问题？用户将来应该避免什么？
4. **决策** — 用户做了哪些选择？为什么选A不选B？
5. **技能信号** — 用户展示了哪些领域的知识？哪些领域明确不熟悉？
6. **工作风格** — 用户倾向简单方案还是复杂方案？一步到位还是渐进式？

## 返回格式（JSON）

{{
  "profile_updates": {{
    "language": "用户使用的语言",
    "technical_level": "非技术/初级/中级/高级",
    "description": "一句话描述用户"
  }},
  "work_style_updates": {{
    "preferences": {{"偏好名": "偏好值"}},
    "communication": "沟通风格描述"
  }},
  "quality_updates": {{
    "acceptance_threshold": 1-5,
    "rules": ["规则1", "规则2"]
  }},
  "lessons": [
    {{"summary": "一句话教训", "detail": "详细说明", "domain": "领域"}}
  ],
  "decisions": [
    {{"question": "决策问题", "choice": "选择", "reasoning": "理由"}}
  ],
  "domains_used": ["python", "frontend"],
  "project_info": {{
    "title": "项目简短标题",
    "tech_stack": ["python", "html"],
    "known_issues": ["已知问题1"]
  }}
}}

只返回 JSON，不要其他文字。只提取实际观察到的内容，没有的字段留空或省略。"""


def extract_knowledge(
    conversation: list[dict],
    project_folder: str,
    project_files: str,
    provider=None,
) -> dict | None:
    """Extract structured knowledge from a conversation using LLM.

    Returns a dict with extracted knowledge, or None on failure.
    """
    if not provider or not conversation:
        return None

    # Build conversation text (last 20 messages to stay within context)
    recent = conversation[-20:]
    conv_text = ""
    for msg in recent:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:500]
        conv_text += f"[{role}]: {content}\n\n"

    prompt = EXTRACTION_PROMPT.format(
        conversation=conv_text[:4000],
        project_folder=project_folder,
        project_files=project_files[:500],
    )

    try:
        messages = [{"role": "user", "content": prompt}]
        raw = provider.chat(messages, project_folder)

        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return None


def ingest_extraction(engram: Engram, extracted: dict,
                      project_folder: str, session_id: str = "") -> dict:
    """Apply extracted knowledge to the Engram. Returns a summary of what was learned."""
    learned: list[str] = []
    source = {"project": project_folder, "session": session_id, "time": _now_iso()}

    # Profile updates
    profile_updates = extracted.get("profile_updates", {})
    if profile_updates:
        # Only update non-empty values
        clean = {k: v for k, v in profile_updates.items() if v}
        if clean:
            engram.update_profile(clean)
            learned.append(f"了解到你的基本信息（{', '.join(clean.keys())}）")

    # Work style updates
    style_updates = extracted.get("work_style_updates", {})
    if style_updates:
        clean = {}
        if style_updates.get("preferences"):
            clean["preferences"] = style_updates["preferences"]
        if style_updates.get("communication"):
            clean["communication"] = style_updates["communication"]
        if clean:
            engram.update_work_style(clean)
            learned.append("了解到你的工作风格偏好")

    # Quality standards
    quality = extracted.get("quality_updates", {})
    if quality:
        clean = {}
        if quality.get("acceptance_threshold"):
            try:
                clean["acceptance_threshold"] = int(quality["acceptance_threshold"])
            except (ValueError, TypeError):
                pass
        if quality.get("rules"):
            existing = engram.get_quality_standards()
            existing_rules = set(existing.get("rules", []))
            new_rules = [r for r in quality["rules"] if r not in existing_rules]
            if new_rules:
                all_rules = list(existing_rules) + new_rules
                clean["rules"] = all_rules[-15:]  # keep last 15 rules
        if clean:
            engram.update_quality_standards(clean)
            learned.append("更新了你的质量标准")

    # Lessons
    lessons = extracted.get("lessons", [])
    for l in lessons[:5]:
        if isinstance(l, dict) and l.get("summary"):
            l["source_project"] = project_folder
            l["source_session"] = session_id
            engram.add_lesson(l)
            learned.append(f"记住了教训: {l['summary'][:40]}")

    # Decisions
    decisions = extracted.get("decisions", [])
    for d in decisions[:5]:
        if isinstance(d, dict) and d.get("question"):
            d["source_project"] = project_folder
            d["source_session"] = session_id
            engram.add_decision(d)
            learned.append(f"记录了决策: {d['question'][:40]}")

    # Domain usage
    domains = extracted.get("domains_used", [])
    for domain in domains[:5]:
        if isinstance(domain, str) and domain:
            engram.increment_domain_usage(domain)

    # Project snapshot
    proj_info = extracted.get("project_info", {})
    if proj_info:
        existing = engram.get_project_snapshot(project_folder)
        session_count = existing.get("session_count", 0) + 1
        proj_info["session_count"] = session_count
        engram.save_project_snapshot(project_folder, proj_info)

    return {
        "items_learned": len(learned),
        "summary": learned,
    }


# ---------------------------------------------------------------------------
# Migration helper: import from old oca_memory.py
# ---------------------------------------------------------------------------

def migrate_from_oca_memory(oca_memory_dir: str, engram: Engram) -> dict:
    """Import knowledge from old .oca/memory/ into Engram.

    One-time migration for existing OCA users.
    """
    mem_dir = Path(oca_memory_dir)
    migrated: list[str] = []

    # Owner profile → Engram profile
    profile_path = mem_dir / "owner_profile.json"
    if profile_path.exists():
        try:
            old_profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if old_profile:
                engram.update_profile({
                    "language": old_profile.get("language", ""),
                    "migrated_from": "oca_memory",
                })
                prefs = old_profile.get("preferences", {})
                if prefs:
                    engram.update_work_style({"preferences": prefs})
                threshold = old_profile.get("quality_threshold")
                if threshold:
                    engram.update_quality_standards({"acceptance_threshold": threshold})
                migrated.append("owner_profile")
        except Exception:
            pass

    # Project patterns → Engram domains + quality standards
    patterns_path = mem_dir / "project_patterns.json"
    if patterns_path.exists():
        try:
            patterns = json.loads(patterns_path.read_text(encoding="utf-8"))
            file_types = patterns.get("common_file_types", {})
            for ext, count in file_types.items():
                domain_map = {
                    ".py": "python", ".js": "javascript", ".ts": "typescript",
                    ".html": "frontend", ".css": "frontend",
                    ".json": "config", ".md": "documentation",
                }
                domain = domain_map.get(ext)
                if domain:
                    engram.update_domain(domain, {"project_count": count})
            migrated.append("project_patterns")
        except Exception:
            pass

    # Near misses → Engram lessons
    nm_path = mem_dir / "near_misses.json"
    if nm_path.exists():
        try:
            near_misses = json.loads(nm_path.read_text(encoding="utf-8"))
            if isinstance(near_misses, list):
                for nm in near_misses[-20:]:
                    engram.add_lesson({
                        "summary": nm.get("what_happened", "")[:80],
                        "detail": nm.get("what_could_have_happened", ""),
                        "domain": "safety",
                        "source_project": "migrated_from_oca_memory",
                    })
                migrated.append(f"near_misses ({len(near_misses)} entries)")
        except Exception:
            pass

    return {"migrated": migrated}


# ---------------------------------------------------------------------------
# OpenClaw Compatibility — SOUL.md / MEMORY.md / USER.md
# ---------------------------------------------------------------------------


def export_to_openclaw(engram: Engram, output_dir: str) -> dict:
    """Export Engram data to OpenClaw format (SOUL.md + MEMORY.md + USER.md).

    Generates three Markdown files that OpenClaw can directly consume.
    This puts Engram at the asset layer — not competing on format, but bridging.

    Args:
        engram: Engram instance.
        output_dir: Directory to write the three files.

    Returns:
        Dict with file paths and status.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    exported = []

    # --- SOUL.md: Agent identity, values, long-term directives ---
    profile = engram.get_profile()
    prefs = engram.get_preferences()
    standards = engram.get_quality_standards()

    soul_lines = [
        "# SOUL",
        "",
        "## Identity",
        f"- Role: {profile.get('role', 'N/A')}",
        f"- Language: {profile.get('language', 'N/A')}",
        f"- Technical Level: {profile.get('technical_level', 'N/A')}",
        f"- Description: {profile.get('description', '')}",
        "",
        "## Work Preferences",
    ]
    work_patterns = prefs.get("work_patterns", {})
    for k, v in work_patterns.items():
        soul_lines.append(f"- {k}: {v}")
    if prefs.get("communication"):
        soul_lines.append(f"- Communication: {prefs['communication']}")

    soul_lines.extend(["", "## Quality Standards"])
    for rule in standards.get("rules", []):
        soul_lines.append(f"- {rule}")

    soul_lines.extend([
        "",
        "## Tool Preferences",
    ])
    for k, v in prefs.get("tool_preferences", {}).items():
        soul_lines.append(f"- {k}: {v}")

    soul_lines.extend([
        "",
        f"_Exported from Engram at {_now_iso()}_",
    ])
    soul_path = out / "SOUL.md"
    soul_path.write_text("\n".join(soul_lines), encoding="utf-8")
    exported.append(str(soul_path))

    # --- USER.md: User personal info ---
    user_lines = [
        "# USER",
        "",
        f"- Role: {profile.get('role', '')}",
        f"- Language: {profile.get('language', '')}",
        f"- Technical Level: {profile.get('technical_level', '')}",
        "",
        f"_Source: Engram ({_now_iso()})_",
    ]
    user_path = out / "USER.md"
    user_path.write_text("\n".join(user_lines), encoding="utf-8")
    exported.append(str(user_path))

    # --- MEMORY.md: Long-term memory (lessons + decisions) ---
    memory_lines = ["# MEMORY", ""]

    lessons = engram.get_lessons(limit=50)
    if lessons:
        memory_lines.append("## Lessons Learned")
        for l in lessons:
            domain = l.get("domain", "")
            prefix = f"[{domain}] " if domain else ""
            memory_lines.append(f"- {prefix}{l.get('summary', '')}")
        memory_lines.append("")

    decisions = engram.get_decisions(limit=30)
    if decisions:
        memory_lines.append("## Key Decisions")
        for d in decisions:
            memory_lines.append(f"- **{d.get('question', '')}**: {d.get('choice', '')}")
            if d.get("reasoning"):
                memory_lines.append(f"  - Why: {d['reasoning'][:100]}")
        memory_lines.append("")

    memory_lines.append(f"_Source: Engram ({_now_iso()})_")
    memory_path = out / "MEMORY.md"
    memory_path.write_text("\n".join(memory_lines), encoding="utf-8")
    exported.append(str(memory_path))

    return {"status": "success", "files": exported}


def import_from_openclaw(
    engram: Engram,
    soul_path: str = "",
    memory_path: str = "",
    user_path: str = "",
) -> dict:
    """Import OpenClaw SOUL.md/MEMORY.md/USER.md into Engram.

    Parses Markdown bullet points into structured Engram data.
    Safe merge: doesn't overwrite existing Engram data, only adds new entries.

    Args:
        engram: Engram instance.
        soul_path: Path to SOUL.md (optional).
        memory_path: Path to MEMORY.md (optional).
        user_path: Path to USER.md (optional).

    Returns:
        Dict with import summary.
    """
    imported = []

    def _parse_md_bullets(text: str) -> list[str]:
        """Extract bullet point content from markdown."""
        lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- "):
                lines.append(stripped[2:].strip())
        return lines

    # --- Import USER.md → profile ---
    if user_path:
        p = Path(user_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            bullets = _parse_md_bullets(content)
            updates = {}
            for b in bullets:
                if b.lower().startswith("role:"):
                    updates["role"] = b.split(":", 1)[1].strip()
                elif b.lower().startswith("language:"):
                    updates["language"] = b.split(":", 1)[1].strip()
                elif b.lower().startswith("technical level:"):
                    updates["technical_level"] = b.split(":", 1)[1].strip()
            if updates:
                engram.update_profile(updates)
                imported.append(f"USER.md → profile ({', '.join(updates.keys())})")

    # --- Import SOUL.md → preferences + quality_standards ---
    if soul_path:
        p = Path(soul_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            # Simple section-based parsing
            current_section = ""
            prefs = {}
            rules = []
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("## "):
                    current_section = stripped[3:].strip().lower()
                elif stripped.startswith("- ") and current_section:
                    value = stripped[2:].strip()
                    if current_section in ("work preferences", "工作偏好"):
                        if ":" in value:
                            k, v = value.split(":", 1)
                            prefs[k.strip()] = v.strip()
                    elif current_section in ("quality standards", "质量标准"):
                        rules.append(value)
            if prefs:
                engram.update_preferences({"work_patterns": prefs})
                imported.append(f"SOUL.md → preferences ({len(prefs)} items)")
            if rules:
                existing = engram.get_quality_standards()
                existing_rules = set(existing.get("rules", []))
                new_rules = [r for r in rules if r not in existing_rules]
                if new_rules:
                    all_rules = list(existing_rules) + new_rules
                    engram.update_quality_standards({"rules": all_rules[-15:]})
                    imported.append(f"SOUL.md → quality_standards (+{len(new_rules)} rules)")

    # --- Import MEMORY.md → lessons ---
    if memory_path:
        p = Path(memory_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            existing_summaries = {
                l.get("summary", "") for l in engram.get_lessons(limit=200)
            }
            new_count = 0
            current_section = ""
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("## "):
                    current_section = stripped[3:].strip().lower()
                elif stripped.startswith("- ") and current_section in (
                    "lessons learned", "经验教训"
                ):
                    text = stripped[2:].strip()
                    # Remove domain prefix like [python]
                    domain = ""
                    if text.startswith("[") and "]" in text:
                        domain = text[1:text.index("]")]
                        text = text[text.index("]") + 1:].strip()
                    if text and text not in existing_summaries:
                        engram.add_lesson({
                            "summary": text,
                            "domain": domain,
                            "source_tool": "openclaw_import",
                        })
                        existing_summaries.add(text)
                        new_count += 1
            if new_count:
                imported.append(f"MEMORY.md → lessons (+{new_count})")

    return {
        "status": "success" if imported else "no_new_data",
        "imported": imported,
    }
